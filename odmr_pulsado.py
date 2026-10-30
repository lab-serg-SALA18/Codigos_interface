# -*- coding: utf-8 -*-

"""
odmr_pulsado.py: ODMR pulsado para o painel do laboratório NV.

Como funciona
--------------
Para cada frequência f a sequência é:

    [preparo com laser] -> [pulso pi em MW] -> [leitura: laser + câmera]

Isso acontece por measure_loops vezes dentro de cada sequência. 
Uma segunda sequência idêntica, mas com a MW desligada, é a referência.
Diferente do ODMR contínuo, a largura da linha é definida pelo pulso: (da ordem de 1/t_pi).


Parâmetros lidos
--------------------
- Pulso pi e potência de MW: calibrados no Rabi.
- Tempo de leitura (em µs): informado ao controlador.
- A temporização LASER_DELAY e o trigger são reutilizados de pulsed_backend.py,
  o mesmo backend usado pelos experimentos pulsados que já funcionam.
- O arquivo ParametrosSequencias.py precisa existir, como no programa antigo.

Como roda
---------
Python 3.6, na mesma pasta dos outros módulos.
"""

import os
import socket
import time

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pyvisa as visa
import PyCapture2
import requests
from scipy.optimize import curve_fit
from tqdm import tqdm

from spinapi import (
    pb_init,
    pb_core_clock,
    pb_get_error,
    pb_stop,
    pb_reset,
    pb_start,
    pb_start_programming,
    pb_stop_programming,
    pb_inst_pbonly,
    pb_close,
    PULSE_PROGRAM,
    LOOP,
    END_LOOP,
    STOP,
)

import configuracoes_lab as cfg
import analise_automatica as analise

# Reutiliza a mesma sequência de pulsos já usada pelo Rabi/Hahn/Ramsey.
# Isso preserva a temporização LASER_DELAY, o trigger e os loops validados.
from pulsed_backend import mw_pulse, measurement_pulse

# Tempo de estabilização do sintetizador após cada comando FREQ.
# O programa antigo aguardava 10 ms; esse tempo não pode depender do shutter.
TEMPO_ESTABILIZACAO_MW_S = 0.010


# Menor duração que a placa aceita por instrução (igual ao NVserver.send_pulses).
TEMPO_MINIMO_NS = 15


# ======================================================================= #
# FÍSICA DO PULSO
# ======================================================================= #

def largura_linha_pulso_mhz(pi_ns):
    """
    Largura à meia altura (MHz) da linha de ressonância de um pulso retangular
    de duração pi_ns que faz uma rotação de pi (Omega * T = pi).
    """
    T = float(pi_ns) * 1e-9
    omega = np.pi / T

    def probabilidade(delta):
        s = np.sqrt(omega ** 2 + delta ** 2)
        return (omega ** 2 / s ** 2) * np.sin(s * T / 2.0) ** 2

    # P(0) = 1, e P cai monotonicamente até ~0 em 1,7 * omega (lóbulo principal).
    lo, hi = 0.0, 1.7 * omega
    for _ in range(200):
        meio = 0.5 * (lo + hi)
        if probabilidade(meio) > 0.5:
            lo = meio
        else:
            hi = meio
    return 2.0 * lo / (2.0 * np.pi) / 1e6


def pulso_para_linha(largura_alvo_mhz, pi_ns_atual):
    """
    Duração de pi (ns) para obter uma linha de largura_alvo_mhz, e quantos dB a
    potência de MW precisa cair em relação ao pi_ns_atual.
    """
    T_novo = 0.795 / (largura_alvo_mhz * 1e6) * 1e9
    reducao_db = 20.0 * np.log10(T_novo / pi_ns_atual)
    return T_novo, reducao_db


def imagem_para_array(imagem):
    """Tira a borda de 1 pixel dos pixels defeituosos."""
    dados = np.frombuffer(imagem.getData(), dtype=np.uint16)
    h = imagem.getCols()
    w = imagem.getRows()
    return dados.reshape((h, w))[1:-1, 1:-1]


def _porta_aberta(porta):
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=0.3):
            return True
    except OSError:
        return False


def _lorentziana(f, f0, g, A, c):
    return c - A * g ** 2 / ((f - f0) ** 2 + g ** 2)



# ======================================================================= #
# CONTROLADOR
# ======================================================================= #

class ODMRPulsado:
    """Varre a frequência de MW com um pulso pi e mede PL(MW on) / PL(MW off)."""

    def __init__(
        self,
        mw_freq_center,
        mw_freq_range,
        mw_freq_step,
        naverage,
        mw_power,
        pi_pulse_time_ns,
        leitura_us,
        progress_callback=None,
    ):
        self.mw_freq_center = float(mw_freq_center)
        self.mw_freq_range = float(mw_freq_range)
        self.naverage = int(naverage)
        self.mw_power = float(mw_power)
        self.pi_pulse_time_ns = float(pi_pulse_time_ns)
        self.leitura_ns = float(leitura_us) * 1000.0
        self.progress_callback = progress_callback or (lambda **kwargs: None)

        if self.pi_pulse_time_ns <= 0:
            raise ValueError("O pulso pi precisa ser maior que zero.")
        if self.leitura_ns <= 0:
            raise ValueError("O tempo de leitura precisa ser maior que zero.")
        if self.naverage < 1:
            raise ValueError("O número de médias precisa ser pelo menos 1.")
        if self.mw_freq_range < 0:
            raise ValueError("A faixa de frequência não pode ser negativa.")

        passo = float(mw_freq_step)
        if passo < 0:
            raise ValueError("O passo não pode ser negativo (use 0 para automático).")
        self.largura_mhz = largura_linha_pulso_mhz(self.pi_pulse_time_ns)
        self.passo_automatico = passo == 0
        self.passo_mhz = passo if passo > 0 else max(self.largura_mhz / 8.0, 0.05)

        self.cancelado = False
        self.mw = None
        self.rm = None
        self.camera = None
        self.spincore_ativa = False
        self._shutter_atual_ms = None

    # ------------------------------------------------------------------ #
    # ORQUESTRAÇÃO
    # ------------------------------------------------------------------ #

    def executar(self):
        resultado = {"sucesso": False}
        try:
            self._verificar_nvserver_livre()
            centro, faixa = self._resolver_faixa()
            freqs = self._grade(centro, faixa)
            self._anunciar_plano(freqs)

            self._iniciar_hardware()
            cal = self._calibrar()
            self._anunciar_calibracao(cal, len(freqs))

            modelo_fundo = self._modelo("back", cal["n_ciclos"])
            fundo_inicio = self._medir(modelo_fundo)[0]
            sinal, t_sinal, t_ref, v_ref = self._varrer(freqs, cal)
            if self.cancelado:
                resultado["mensagem"] = "ODMR pulsado cancelado pelo usuário."
                return resultado
            fundo_fim = self._medir(modelo_fundo)[0]
            fundo = 0.5 * (fundo_inicio + fundo_fim)

            pl, desvio = self._espectro(sinal, t_sinal, t_ref, v_ref, fundo)
            vales = analise.detectar_ressonancias(freqs, pl)
            vales = self._ajustar_vales(freqs, pl, vales)
            fig, caminho_png, caminho_csv = self._salvar(freqs, pl, desvio, vales)

            resultado.update({
                "sucesso": True,
                "df": pd.DataFrame({"mw_freq": freqs, "PL_normalizado": pl, "PL_desvio": desvio}),
                "ressonancias": vales,
                "melhor_ressonancia": vales[0] if vales else None,
                "figura": fig,
                "caminho_grafico": caminho_png,
                "caminho_dados": caminho_csv,
                "largura_pulso_mhz": self.largura_mhz,
                "passo_mhz": self.passo_mhz,
            })
            return resultado

        except Exception as e:
            resultado["mensagem"] = str(e)
            self.progress_callback(mensagem="Erro: %s" % e)
            return resultado

        finally:
            self._encerrar_hardware()

    def _checar_cancelamento(self):
        if self.cancelado:
            raise RuntimeError("Cancelado pelo usuário.")

    # ------------------------------------------------------------------ #
    # PLANO DA VARREDURA
    # ------------------------------------------------------------------ #

    def _verificar_nvserver_livre(self):
        for porta in (65432, 65433):
            if _porta_aberta(porta):
                raise RuntimeError(
                    "O NVserver.py está rodando (porta %d aberta). Feche o servidor das "
                    "sequências antes do ODMR pulsado: só um processo pode usar a SpinCore." % porta
                )

    def _resolver_faixa(self):
        return self.mw_freq_center, self.mw_freq_range


    def _grade(self, centro, faixa):
        n = int(np.floor(2.0 * faixa / self.passo_mhz + 1e-9)) + 1
        return centro - faixa + self.passo_mhz * np.arange(max(n, 3))

    def _anunciar_plano(self, freqs):
        self.progress_callback(mensagem=(
            "Linha pelo pulso pi (%g ns): ≈ %.1f MHz. Passo %s: %.2f MHz, %d pontos."
            % (self.pi_pulse_time_ns, self.largura_mhz,
               "automático" if self.passo_automatico else "manual",
               self.passo_mhz, len(freqs))
        ))
        print(f"Linha pelo pulso pi ({self.pi_pulse_time_ns} ns): ≈ {self.largura_mhz:.1f} MHz. Passo: {self.passo_mhz:.2f} MHz, {len(freqs)} pontos.")
        if self.passo_mhz > self.largura_mhz / 2.0:
            self.progress_callback(mensagem="Aviso: passo grosso para essa linha; os vales ficarão mal amostrados.")
        if self.largura_mhz > 3.0:
            T_novo, reducao = pulso_para_linha(1.5, self.pi_pulse_time_ns)
            self.progress_callback(mensagem=(
                "Para linhas de ~1,5 MHz, o pulso pi seria ≈ %.0f ns, com a potência ~%.0f dB abaixo da atual." % (T_novo, reducao)))
            print(f"Para linhas de ~1,5 MHz, o pulso pi seria ≈ {T_novo:.0f} ns, com a potência ~{reducao:.0f} dB abaixo da atual.")

    # ------------------------------------------------------------------ #
    # HARDWARE
    # ------------------------------------------------------------------ #

    def _iniciar_hardware(self):
        self._iniciar_spincore()
        self._conectar_mw()
        self._definir_potencia_mw(self.mw_power)
        self._iniciar_camera()

    def _iniciar_spincore(self):
        if pb_init() != 0:
            raise RuntimeError(pb_get_error())
        pb_core_clock(300.0)
        self.spincore_ativa = True
        # começa com tudo apagado, para o trigger da câmera ter uma borda de subida
        self._programar([[0b111000000000000000000000, STOP, 0, TEMPO_MINIMO_NS]])

    def _programar(self, pulsos):
        """Carrega a sequência na placa e começa a rodar. Mesma lógica do NVserver.send_pulses."""
        pb_stop()
        pb_reset()
        pb_start_programming(PULSE_PROGRAM)
        for i, p in enumerate(pulsos):
            try:
                duracao = int(max(round(float(p[3])), TEMPO_MINIMO_NS))
                pb_inst_pbonly(p[0], p[1], p[2], duracao)
            except Exception as e:
                try:
                    pb_stop_programming()
                except Exception:
                    pass
                raise RuntimeError("Falha ao programar a instrução %d: %s (%s)" % (i, p, e))
        pb_stop_programming()
        pb_start()

    def _conectar_mw(self):
        self.rm = visa.ResourceManager()
        self.mw = self.rm.open_resource("GPIB0::27::INSTR")

    def _definir_frequencia_mw(self, freq_mhz):
        self.mw.write("FREQ %.6f MHz" % freq_mhz)

    def _definir_potencia_mw(self, dbm):
        if dbm > 5:
            raise ValueError("Potência de %g dBm acima do limite de 5 dBm (configuracoes_lab.py)." % (dbm))
        self.mw.write(f"AMPR {min(dbm, 5)} dBm")

    def _iniciar_camera(self):
        self.camera = PyCapture2.Camera()
        bus = PyCapture2.BusManager()
        self.camera.connect(bus.getCameraFromIndex(0))

        config = self.camera.getConfiguration()
        config.grabTimeout = 5000
        self.camera.setConfiguration(config)

        # ganho manual: o mesmo valor que os experimentos pulsados usam
        ganho = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.GAIN)
        ganho.autoManualMode = False
        ganho.onOff = True
        ganho.absControl = True
        ganho.absValue = float(-2.815)
        self.camera.setProperty(ganho)

        self._configurar_trigger(ligar=False)
        time.sleep(0.3)
        self._configurar_trigger(ligar=True)
        time.sleep(0.3)
        self.camera.startCapture()

    def _configurar_trigger(self, ligar):
        trigger = self.camera.getTriggerMode()
        trigger.onOff = bool(ligar)
        if ligar:
            trigger.polarity = 1  # ativo em nível alto
            trigger.mode = 0
            trigger.source = 0
            trigger.parameter = 0
        self.camera.setTriggerMode(trigger)

    def _ajustar_shutter(self, duracao_ms):
        """Recalcula o shutter para a sequência que vai rodar. Não mexe na câmera se não mudou."""
        if self._shutter_atual_ms is not None and abs(duracao_ms - self._shutter_atual_ms) < 1e-6:
            return
        shutter = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.SHUTTER)
        shutter.autoManualMode = False
        shutter.onOff = True
        shutter.absControl = True
        shutter.absValue = float(duracao_ms)
        self.camera.setProperty(shutter)
        self._shutter_atual_ms = float(duracao_ms)

    def _medir_bruto(self, modelo):
        """
        Dispara uma leitura e devolve (soma da região útil, pixel mais forte, tempo do ciclo em s).
        O shutter é ajustado antes de programar, para que a exposição já use a duração certa.
        """
        pulsos, duracao_ns = modelo
        self._ajustar_shutter(duracao_ns * 1e-6)
        self._programar(pulsos)
        inicio = time.time()
        imagem = self.camera.retrieveBuffer()  # levanta Fc2error se o frame não chegar
        t_ciclo = time.time() - inicio
        roi = imagem_para_array(imagem)
        return float(np.sum(roi)), float(np.max(roi)), t_ciclo

    def _medir(self, modelo):
        try:
            return self._medir_bruto(modelo)
        except PyCapture2.Fc2error as e:
            raise RuntimeError(
                "A câmera não entregou o frame (%s). Confira o cabo de trigger e se o laser dispara." % e)

    def _encerrar_hardware(self):
        """MW desligada, câmera parada, placa com tudo apagado e fechada. Roda sempre."""
        if self.mw is not None:
            try:
                self._definir_potencia_mw(-100)
            except Exception:
                pass
            try:
                self.mw.close()
            except Exception:
                pass
            self.mw = None
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
            self.rm = None
        if self.camera is not None:
            for funcao in (lambda: self._configurar_trigger(ligar=False),
                           self.camera.stopCapture, self.camera.disconnect):
                try:
                    funcao()
                except Exception:
                    pass
            self.camera = None
        if self.spincore_ativa:
            try:
                self._programar([[0b111000000000000000000000, STOP, 0, TEMPO_MINIMO_NS]])
            except Exception:
                pass
            for funcao in (pb_stop, pb_close):
                try:
                    funcao()
                except Exception:
                    pass
            self.spincore_ativa = False

    # ------------------------------------------------------------------ #
    # CALIBRAÇÃO DA LEITURA
    # ------------------------------------------------------------------ #

    def _duracao_real_sequencia_ns(self, pulsos, n_ciclos):
        """Duração física: preâmbulo + N vezes o bloco LOOP..END_LOOP + cauda."""
        idx_loop = next((i for i, pulso in enumerate(pulsos) if pulso[1] == LOOP), None)
        if idx_loop is None:
            raise RuntimeError("A sequência do pulsed_backend não contém LOOP.")

        idx_end = next(
            (i for i in range(idx_loop + 1, len(pulsos)) if pulsos[i][1] == END_LOOP),None,)
        if idx_end is None:
            raise RuntimeError("A sequência do pulsed_backend não contém END_LOOP.")

        pre_ns = sum(int(round(p[3])) for p in pulsos[:idx_loop])
        ciclo_ns = sum(int(round(p[3])) for p in pulsos[idx_loop:idx_end + 1])
        cauda_ns = sum(int(round(p[3])) for p in pulsos[idx_end + 1:])
        return int(pre_ns + int(n_ciclos) * ciclo_ns + cauda_ns)

    def _modelo(self, tipo, n_ciclos):
        """Monta a sequência com o backend já usado nos experimentos pulsados."""
        if tipo not in ("mw_on", "mw_off", "back"):
            raise ValueError("Tipo de sequência desconhecido: %r" % (tipo,))

        # A mesma sequência validada no programa antigo: pulso MW + measurement_pulse.
        # measurement_pulse aplica LASER_DELAY, faz a leitura e desliga MW/laser
        # corretamente conforme o tipo de foto.
        pulsos = [mw_pulse(int(round(self.pi_pulse_time_ns)))]
        pulsos, _duracao_ms_backend = measurement_pulse(
            pulsos,
            tipo,
            int(round(self.leitura_ns)),
            int(n_ciclos),
        )
        duracao_ns = self._duracao_real_sequencia_ns(pulsos, n_ciclos)
        return pulsos, duracao_ns

    def _ciclos_para(self, duracao_ms):
        """Calcula N a partir do tempo físico do bloco LOOP..END_LOOP."""
        _, duracao_1 = self._modelo("mw_off", 1)
        _, duracao_2 = self._modelo("mw_off", 2)
        ciclo_ns = duracao_2 - duracao_1
        fixo_ns = duracao_1 - ciclo_ns

        if ciclo_ns <= 0:
            raise RuntimeError("Duração do ciclo inválida durante a calibração.")

        n = int(round((float(duracao_ms) * 1e6 - fixo_ns) / float(ciclo_ns)))
        return max(1, n)

    def _calibrar(self):
        """
        Escolhe a duração de cada leitura da câmera.

        1) Um frame de teste (sem MW) de CAL_MS dá o brilho do pixel mais forte
           e o tempo que a câmera gasta por frame (disparo mais transferência).
        2) A leitura é a menor entre: a que deixa o pixel mais forte em
           FRACAO_SATURACAO da escala, e a que deixa a câmera com no máximo
           FRACAO_CAMERA_MAX do tempo. Respeita LEITURA_MIN e LEITURA_MAX.
        """
        n_cal = self._ciclos_para(2.0)
        modelo_cal = self._modelo("mw_off", n_cal)
        dur_cal_ms = modelo_cal[1] * 1e-6

        # A câmera pode perder o primeiro trigger. Tenta de novo antes de desistir.
        for tentativa in range(1, 6):
            self._checar_cancelamento()
            try:
                self._medir_bruto(modelo_cal)
                break
            except PyCapture2.Fc2error as e:
                self.progress_callback(
                    mensagem="Câmera ainda não respondeu ao trigger (tentativa %d): %s" % (tentativa, e)
                )
                time.sleep(0.5)
        else:
            raise RuntimeError("A câmera não respondeu ao trigger. Confira o cabo de trigger.")

        _, pico, t_ciclo = self._medir(modelo_cal)
        # O pixel nunca vale zero, porque a câmera tem nível de escuro. Por isso
        # comparo com o fundo medido no mesmo tempo de leitura.
        _, pico_fundo, _ = self._medir(self._modelo("back", n_cal))
        sinal_pico = pico - pico_fundo
        if sinal_pico <= 0:
            raise RuntimeError("A câmera não viu luz na calibração. Confira laser, AOM, trigger e foco.")

        avisos = []
        if sinal_pico < 0.05 * pico:
            avisos.append("o sinal é pequeno perto do fundo: confira o foco e a potência do laser.")
        escala = 65535
        limite_sat_ms = dur_cal_ms * 0.5 * escala / pico
        sobrecarga_ms = max(t_ciclo * 1e3 - dur_cal_ms, 0.0)

        alvo_cam_ms = sobrecarga_ms / 0.10
        alvo_ms = min(max(alvo_cam_ms, 2.0), 100.0)

        if limite_sat_ms < 2.0:
            alvo_ms = 2.0
            avisos.append("o pixel mais forte satura até na leitura mínima: reduza a potência do laser ou o ganho.")
        elif alvo_ms > limite_sat_ms:
            alvo_ms = limite_sat_ms
            avisos.append("leitura limitada pela saturação da câmera.")

        n_med = self._ciclos_para(alvo_ms)
        dur_med_ms = self._modelo("mw_on", n_med)[1] * 1e-6
        fracao_camera = sobrecarga_ms / (sobrecarga_ms + dur_med_ms)
        if fracao_camera > 0.10 + 0.02:
            avisos.append("a câmera ocupa %.0f%% do tempo (limite %.0f%%): ela é o gargalo da medida."
                          % (100 * fracao_camera, 100 * 0.10))

        return {
            "n_ciclos": n_med,
            "duracao_ms": dur_med_ms,
            "sobrecarga_ms": sobrecarga_ms,
            "fracao_camera": fracao_camera,
            "fracao_saturacao": pico * (dur_med_ms / dur_cal_ms) / escala,
            "avisos": avisos,
        }

    def _ciclo_us(self):
        _, d1 = self._modelo("mw_off", 1)
        _, d2 = self._modelo("mw_off", 2)
        return (d2 - d1) / 1000.0

    def _anunciar_calibracao(self, cal, n_freq):
        self.progress_callback(mensagem=(
            "Leitura: %.2f ms (%d ciclos de %.2f µs). Câmera ocupa %.0f%% do tempo; "
            "pixel mais forte em %.0f%% da saturação."
            % (cal["duracao_ms"], cal["n_ciclos"], self._ciclo_us(),
               100 * cal["fracao_camera"], 100 * cal["fracao_saturacao"])
        ))
        for aviso in cal["avisos"]:
            self.progress_callback(mensagem="Aviso: " + aviso)

    # ------------------------------------------------------------------ #
    # VARREDURA
    # ------------------------------------------------------------------ #

    def _publicar(self, barra):
        dados = barra.format_dict
        taxa = dados.get("rate")
        restante = None
        if taxa and taxa > 0 and barra.total:
            restante = max(0.0, (barra.total - barra.n) / taxa)
        self.progress_callback(
            progresso=barra.n / float(barra.total),
            tempo_decorrido=float(dados.get("elapsed", 0.0)),
            tempo_restante=restante,
        )

    def _varrer(self, freqs, cal):
        """
        Mede o sinal (MW on) em cada frequência, em cada média. A referência
        (MW off) é medida a cada N pontos, e o instante de cada uma é guardado
        para interpolar a deriva depois.
        """
        modelo_on = self._modelo("mw_on", cal["n_ciclos"])
        modelo_ref = self._modelo("mw_off", cal["n_ciclos"])
        n_f = len(freqs)
        refs_por_media = -(-n_f // 1)
        # Estabilização do gerador de MW, independente do tempo de exposição da câmera.
        estabilizacao_s = TEMPO_ESTABILIZACAO_MW_S

        barra = tqdm(total=self.naverage * (n_f + refs_por_media), desc="ODMR pulsado",
                     unit="leitura", mininterval=1, leave=False)
        sinal = np.zeros((self.naverage, n_f))
        t_sinal = np.zeros((self.naverage, n_f))
        t_ref, v_ref = [], []
        contador = 0
        try:
            for media in range(self.naverage):
                if self.cancelado:
                    break
                for idx, f in enumerate(freqs):
                    if self.cancelado:
                        break
                    if idx % 1 == 0:
                        v_ref.append(self._medir(modelo_ref)[0])
                        t_ref.append(contador)
                        contador += 1
                        barra.update(1)
                        self._publicar(barra)

                    self._definir_frequencia_mw(f)
                    time.sleep(estabilizacao_s)
                    sinal[media, idx] = self._medir(modelo_on)[0]
                    t_sinal[media, idx] = contador
                    contador += 1
                    barra.update(1)
                    self._publicar(barra)
                if not self.cancelado:
                    self.progress_callback(mensagem="Média %d/%d concluída." % (media + 1, self.naverage))
        finally:
            barra.close()
        return sinal, t_sinal, np.array(t_ref, dtype=float), np.array(v_ref, dtype=float)

    # ------------------------------------------------------------------ #
    # ANÁLISE
    # ------------------------------------------------------------------ #

    def _espectro(self, sinal, t_sinal, t_ref, v_ref, fundo):
        """
        Razão (sinal - fundo) / (referência - fundo) em cada ponto, com a referência
        interpolada no instante da medida. Depois média entre as médias e
        normalização pelo máximo, como no ODMR contínuo.
        """
        if len(t_ref) == 0:
            raise RuntimeError("Nenhuma referência foi medida.")
        razoes = np.empty_like(sinal)
        for m in range(sinal.shape[0]):
            ref = np.interp(t_sinal[m], t_ref, v_ref)
            with np.errstate(divide="ignore", invalid="ignore"):
                razoes[m] = (sinal[m] - fundo) / (ref - fundo)
        razoes[~np.isfinite(razoes)] = np.nan
        media = np.nanmean(razoes, axis=0)
        desvio = np.nanstd(razoes, axis=0) if razoes.shape[0] > 1 else np.zeros(razoes.shape[1])
        maximo = np.nanmax(media)
        if not np.isfinite(maximo) or maximo <= 0:
            raise RuntimeError("Espectro sem sinal positivo: confira o fundo e a referência.")
        return media / maximo, desvio / maximo

    def _ajustar_vales(self, freqs, pl, vales):
        """
        Ajusta uma Lorentziana em cada vale detectado, numa janela de ±1,5 larguras
        de linha. Assim a posição sai com precisão melhor que o passo. Se o ajuste
        falhar, fica o ponto da grade, sem largura.
        """
        saida = []
        janela = 1.5 * self.largura_mhz
        g0 = 0.5 * self.largura_mhz
        for v in vales:
            f_i, y_i = v["freq"], v["valor"]
            resultado = {
                "freq": float(f_i), "freq_grade": float(f_i), "valor": float(y_i),
                "contraste": float(v["contraste"]), "fwhm_mhz": float("nan"), "ajuste_ok": False,
            }
            m = np.abs(freqs - f_i) <= janela
            if m.sum() >= 6:
                A0 = min(max(1.0 - y_i, 1e-3), 1.45)
                lo = np.array([f_i - self.largura_mhz, 0.2 * g0, 0.0, 0.8])
                hi = np.array([f_i + self.largura_mhz, 3.0 * g0, 1.5, 1.3])
                try:
                    popt, _ = curve_fit(
                        _lorentziana, freqs[m], pl[m],
                        p0=[f_i, g0, A0, 1.0],
                        bounds=(lo, hi),
                        maxfev=5000,
                    )
                    # Recusa o ajuste se algum parâmetro encostou no limite, ou se a
                    # Lorentziana explica pouco da janela (ruído, não um vale).
                    folga = 1e-3 * (hi - lo)
                    no_limite = np.any(popt - lo < folga) or np.any(hi - popt < folga)
                    y_obs = pl[m]
                    ss_res = float(np.sum((y_obs - _lorentziana(freqs[m], *popt)) ** 2))
                    ss_tot = float(np.sum((y_obs - y_obs.mean()) ** 2))
                    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
                    if not no_limite and r2 >= 0.5:
                        resultado.update({
                            "freq": float(popt[0]),
                            "fwhm_mhz": float(2.0 * abs(popt[1])),
                            "contraste": float(popt[2]),
                            "ajuste_ok": True,
                            "r2": r2,
                            "parametros": tuple(float(p) for p in popt),
                        })
                except (RuntimeError, ValueError):
                    pass
            saida.append(resultado)
        saida.sort(key=lambda d: d["contraste"], reverse=True)
        return saida

    # ------------------------------------------------------------------ #
    # SAÍDA
    # ------------------------------------------------------------------ #

    def _aviso_telegram(self, mensagem, caminho_imagem=None):
        if not cfg.TELEGRAM_ATIVO_PADRAO:
            return
        try:
            base = "https://api.telegram.org/bot%s" % cfg.TELEGRAM_TOKEN
            requests.post(base + "/sendMessage",
                          json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": mensagem}, timeout=10)
            if caminho_imagem and os.path.isfile(caminho_imagem):
                with open(caminho_imagem, "rb") as foto:
                    requests.post(base + "/sendPhoto", data={"chat_id": cfg.TELEGRAM_CHAT_ID},
                                  files={"photo": foto}, timeout=30)
        except Exception as e:
            self.progress_callback(mensagem="[Telegram] Falha ao enviar aviso: %s" % e)

    def _salvar(self, freqs, pl, desvio, vales):
        pasta = os.path.join(cfg.PASTA_DADOS_BASE, "ODMR_PULSADO", "Dados", time.strftime("%d_%m_%Y"))
        os.makedirs(pasta, exist_ok=True)
        nome = (f"ODMR_PULSADO_f{self.mw_freq_center:.1f}MHz_{self.mw_freq_range:.1f}range_{self.passo_mhz:.2f}step_{self.naverage}"
                f"averages_pi{self.pi_pulse_time_ns}ns_leitura{self.leitura_ns / 1000.0:.1f}us_{time.strftime('%Y-%m-%d_%H-%M')}")
        caminho_csv = os.path.join(pasta, nome + ".csv")
        caminho_png = os.path.join(pasta, nome + ".png")
        pd.DataFrame({"mw_freq": freqs, "PL_normalizado": pl, "PL_desvio": desvio}).to_csv(
            caminho_csv, index=False)

        fig = plt.figure(figsize=(16, 9))
        plt.plot(freqs, pl, linestyle="--", linewidth=1, color="black")
        plt.scatter(freqs, pl,facecolor="red",edgecolor="black",zorder=3,)
        for v in vales:
            if v["ajuste_ok"]:
                f0, g, A, c = v["parametros"]
                x = np.linspace(f0 - 1.5 * self.largura_mhz, f0 + 1.5 * self.largura_mhz, 200)
                plt.plot(x, _lorentziana(x, f0, g, A, c), "-", color="crimson", linewidth=1.5, alpha=0.8)
            plt.axvline(v["freq"], color="gray", linestyle=":", alpha=0.5)

        if vales:
            melhor = vales[0]
            idx = int(np.argmin(np.abs(freqs - melhor["freq_grade"])))
            plt.axvline(melhor["freq"], color="blue", linewidth=2, alpha=0.7)
            plt.annotate(
                "%.2f MHz\ncontraste %.1f%%" % (melhor["freq"], melhor["contraste"] * 100),
                xy=(melhor["freq_grade"], pl[idx]), xytext=(10, -30), textcoords="offset points",
                fontsize=12, color="blue", arrowprops=dict(arrowstyle="->", color="blue"), )

        plt.xlabel("Frequência de MW (MHz)", fontsize=14)
        plt.ylabel("PL total", fontsize=14)
        plt.title("ODMR pulsado: pi = %g ns, leitura = %g µs, linha pelo pulso ≈ %.1f MHz"
                  % (self.pi_pulse_time_ns, self.leitura_ns / 1000.0, self.largura_mhz), fontsize=15)
        plt.tight_layout()
        plt.savefig(caminho_png)

        if vales:
            melhor = vales[0]
            msg = ("🚀 ODMR pulsado de %s concluído! Ressonância de maior contraste: %.2f MHz "
                   "(contraste %.1f%%, largura %.1f MHz)"
                   % (time.strftime("%H:%M"), melhor["freq"], melhor["contraste"] * 100,
                      melhor["fwhm_mhz"] if melhor["ajuste_ok"] else self.largura_mhz))
        else:
            msg = "🚀 ODMR pulsado de %s concluído (nenhum vale claro encontrado)." % time.strftime("%H:%M")
        self._aviso_telegram(msg, caminho_png)
        return fig, caminho_png, caminho_csv

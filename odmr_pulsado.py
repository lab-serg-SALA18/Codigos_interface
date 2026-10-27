# -*- coding: utf-8 -*-
"""
ODMR pulsado

Como funciona
-------------
Para cada frequência f a sequência é:

    [preparo com laser] -> [pulso pi em f, MW ligada] -> [leitura: laser + câmera]

repetida measure_loops vezes dentro de cada sequência. Uma segunda sequência
idêntica, mas com a MW desligada, é a referência. A razão
PL(MW on) / PL(MW off) cai quando f está na ressonância, porque o pulso pi
leva o spin para um estado que emite menos luz na leitura.


Por que pulsado
---------------
A largura da linha vem do pulso (da ordem de 1/T_pi), não da potência de MW.
Com pi de ~170 ns a resolução fica em alguns MHz, e o contraste costuma ser
maior que no CW, porque o spin só é perturbado durante o pulso.

Como se encaixa no seu setup
----------------------------
- Os pulsos são montados com mw_pulse() e measurement_pulse() do
  pulsed_backend.py, as mesmas funções que o Rabi, o HAN e o RAM usam. O
  preparo de laser, o trigger da câmera e o loop de leitura são idênticos aos
  que já funcionam.
- A grade de frequências é a do odmr_controller.py (centro ± faixa), então os
  espectros CW e pulsado caem nos mesmos pontos e podem ser comparados.
- Fala direto com a SpinCore pelo spinapi, sem Qiskit e sem NVserver.py. Por
  isso o NVserver.py não pode estar aberto (só um processo abre a placa), e não
  há custo de transpilação por ponto.

Como roda
---------
Python 3.6, na mesma pasta dos outros módulos. O pulsed_backend.py importa o
ParametrosSequencias.py, que o painel já gera; ele só precisa existir. Os
valores PI_PULSE_TIME e MEASURE_LOOPS dele NÃO são usados aqui: valem os
parâmetros desta medida.
"""

import os
import time

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pyvisa as visa
import PyCapture2
import requests

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
    ns,
    us,
)

import configuracoes_lab as cfg
import analise_automatica as analise

# Montagem dos pulsos: as mesmas funções que o Rabi, o HAN e o RAM usam.
from pulsed_backend import mw_pulse, measurement_pulse


# Espera depois de trocar a frequência do sintetizador e antes de disparar a
# sequência. Mesmo critério do odmr_controller.py (~8 ms de delay + folga).
TEMPO_ESTABILIZACAO_MW_S = 0.01

# Menor duração de pulso que a placa aceita. Igual ao NVserver.send_pulses.
DURACAO_MINIMA_NS = 15

# Quantas tentativas dar na primeira captura, depois de ligar o trigger.
TENTATIVAS_AQUECIMENTO = 5


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
        measure_loops,
        laser_time_us,
        dynamic_shutter=True,
        usar_background=False,
        progress_callback=None,
    ):
        self.mw_freq_center = mw_freq_center
        self.mw_freq_range = mw_freq_range
        self.mw_freq_step = mw_freq_step
        self.naverage = int(naverage)
        self.mw_power = mw_power

        # Duração do pulso pi. Use o valor calibrado no Rabi, na mesma potência
        # de MW, porque o pi depende da potência.
        self.pi_pulse_time_ns = float(pi_pulse_time_ns)
        self.pi_time = self.pi_pulse_time_ns * ns

        # Duração da leitura com laser. Vem em µs da interface; o multiplicador
        # us é o mesmo que o ParametrosSequencias.py usa.
        self.laser_time = float(laser_time_us) * us
        self.measure_loops = int(measure_loops)

        # Shutter da câmera. Modo dinâmico: duração da sequência (como nos
        # outros experimentos pulsados).
        self.dynamic_shutter = dynamic_shutter


        # Com fundo, a leitura sem laser e sem MW entra na razão. Sem fundo, a
        # fórmula vira simplesmente on/off, como o Rabi faz com o fundo zerado.
        self.usar_background = usar_background

        # Mesma grade do odmr_controller.py: centro ± faixa, com passo mw_freq_step.
        self.mw_freqs = np.arange(
            mw_freq_center - mw_freq_range,
            mw_freq_center + mw_freq_range + mw_freq_step,
            mw_freq_step,
        )

        self.mw = None
        self.rm = None
        self.camera = None
        self.spincore_ativa = False
        self.cancelado = False
        self.progress_callback = progress_callback or (lambda **kwargs: None)

    # ------------------------------------------------------------------ #
    # SEQUÊNCIAS
    # ------------------------------------------------------------------ #

    def _montar_sequencia(self, tipo_foto):
        """
        Monta a lista de pulsos de uma medida e devolve (pulsos, duracao_ms).

        tipo_foto:
          'mw_on'  -> pulso pi ligado. É o sinal.
          'mw_off' -> mesma sequência com a chave de MW desligada. É a
                      referência. measurement_pulse zera o bit da MW em todos
                      os pulsos, então o tempo total é igual ao do mw_on.
          'back'   -> sem laser e sem MW (fundo do sensor), mesmo tempo total.
        """
        pulsos = [mw_pulse(self.pi_time)]
        pulsos, duracao_ms = measurement_pulse(pulsos, tipo_foto, self.laser_time, self.measure_loops)
        return pulsos, duracao_ms

    def _programar(self, pulsos):
        """
        Carrega a sequência na SpinCore e começa a rodar.

        É a mesma lógica do send_pulses do NVserver.py, só que local (sem
        socket): para, reseta, programa pulso a pulso, fecha e inicia.
        """
        pb_stop()
        pb_reset()
        pb_start_programming(PULSE_PROGRAM)

        for i, pulso in enumerate(pulsos):
            try:
                pb_inst_pbonly(*pulso[:3], int(max(pulso[3], DURACAO_MINIMA_NS)))
            except Exception as e:
                try:
                    pb_stop_programming()
                except Exception:
                    pass
                raise RuntimeError(f"Falha ao programar o pulso {i}: {pulso} ({e})")

        pb_stop_programming()
        pb_start()

    def _medir(self, pulsos):
        """Dispara uma sequência, captura um frame e devolve a soma da região útil."""
        self._programar(pulsos)
        imagem = self.camera.retrieveBuffer()
        return float(np.sum(self._imagem_para_array(imagem)))

    @staticmethod
    def _imagem_para_array(imagem):
        dados = np.frombuffer(imagem.getData(), dtype=np.uint16)
        h = imagem.getCols()
        w = imagem.getRows()
        matriz = dados.reshape((h, w))
        return matriz[1:-1, 1:-1]

    # ------------------------------------------------------------------ #
    # HARDWARE
    # ------------------------------------------------------------------ #

    def _iniciar_spincore(self):
        if pb_init() != 0:
            raise RuntimeError(pb_get_error())
        pb_core_clock(300.0)
        self.spincore_ativa = True

    def _parar_spincore(self):
        if not self.spincore_ativa:
            return
        for funcao in (pb_stop, pb_close):
            try:
                funcao()
            except Exception:
                pass
        self.spincore_ativa = False

    def _conectar_mw(self):
        self.rm = visa.ResourceManager()
        self.mw = self.rm.open_resource(cfg.MW_RESOURCE_NAME)

    def _definir_frequencia_mw(self, freq_mhz):
        self.mw.write(f"FREQ {freq_mhz} MHz")

    def _definir_potencia_mw(self, power_dbm):
        if power_dbm > 0:
            raise ValueError("Potência de MW perigosa (>0 dBm) — abortado por segurança.")
        self.mw.write(f"AMPR {min(power_dbm, 0)} dBm")

    def _parar_mw(self):
        """Desliga a potência e fecha o recurso VISA com segurança."""
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

    def _iniciar_camera(self, shutter_ms):
        self.camera = PyCapture2.Camera()
        bus = PyCapture2.BusManager()
        self.camera.connect(bus.getCameraFromIndex(0))

        config = self.camera.getConfiguration()
        config.grabTimeout = 5000
        self.camera.setConfiguration(config)

        # Shutter manual. Com o modo dinâmico, o valor é a duração da sequência.
        shutter = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.SHUTTER)
        shutter.autoManualMode = False
        shutter.onOff = True
        shutter.absControl = True
        shutter.absValue = float(shutter_ms)
        self.camera.setProperty(shutter)

        # Trigger externo: a câmera só captura quando o pulso camera_on (bit 12)
        # chega. Desliga, espera e liga de novo, como no experimento_sequencia.py.
        self._configurar_trigger(ligar=False)
        time.sleep(0.3)
        self._configurar_trigger(ligar=True)
        time.sleep(0.3)

        self.camera.startCapture()

    def _configurar_trigger(self, ligar):
        trigger = self.camera.getTriggerMode()
        trigger.onOff = ligar
        if ligar:
            trigger.polarity = 1  # nível alto
            trigger.mode = 0
            trigger.source = 0
            trigger.parameter = 0
        self.camera.setTriggerMode(trigger)

    def _aquecer_camera(self, pulsos_referencia):
        """
        A primeira captura logo depois de ligar o trigger pode dar timeout se a
        câmera ainda não estiver armada. Tenta algumas vezes e descarta o frame.
        """
        for tentativa in range(1, TENTATIVAS_AQUECIMENTO + 1):
            if self.cancelado:
                raise RuntimeError("Cancelado durante a inicialização da câmera.")
            try:
                self._medir(pulsos_referencia)
                return
            except PyCapture2.Fc2error as e:
                self.progress_callback(mensagem=f"Câmera ainda não respondeu ao trigger (tentativa {tentativa}): {e}")
                time.sleep(0.5)

        raise RuntimeError("A câmera não respondeu ao trigger. Confira o cabo de trigger antes de tentar de novo.")

    def _parar_camera(self):
        if self.camera is None:
            return
        for funcao in (
            lambda: self._configurar_trigger(ligar=False),
            self.camera.stopCapture,
            self.camera.disconnect,
        ):
            try:
                funcao()
            except Exception:
                pass
        self.camera = None

    # ------------------------------------------------------------------ #
    # PROCESSAMENTO
    # ------------------------------------------------------------------ #

    def _calcular_espectro(self, soma_on, soma_off, soma_back):
        """
        Converte as somas acumuladas no espectro normalizado.

        Usa a mesma fórmula do Rabi (experimento_sequencia.processar_dados):
            razão = (on - fundo) / (off - fundo)
        Depois normaliza pelo máximo, como o ODMR CW. Fora da ressonância a curva
        fica perto de 1, e a ressonância aparece como vale abaixo disso.
        """
        n = self.naverage
        on = soma_on / n
        off = soma_off / n
        fundo = soma_back / n

        denominador = off - fundo


        razao = (on - fundo) / denominador
        razao = razao / np.max(razao)

        return pd.DataFrame({
            "mw_freq": self.mw_freqs,
            "PL_mw_on": on,
            "PL_mw_off": off,
            "PL": razao,
        })

    # ------------------------------------------------------------------ #
    # SAÍDA
    # ------------------------------------------------------------------ #

    def _criar_pasta_saida(self):
        caminho = os.path.join(
            cfg.PASTA_DADOS_BASE, "ODMR_PULSADO", "Dados", time.strftime("%d_%m_%Y"))
        os.makedirs(caminho, exist_ok=True)
        return caminho

    def _aviso_telegram(self, mensagem, caminho_imagem=None):
        if not cfg.TELEGRAM_ATIVO_PADRAO:
            return
        try:
            base = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}"
            requests.post(
                f"{base}/sendMessage",
                json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": mensagem},
                timeout=10,)
            if caminho_imagem and os.path.isfile(caminho_imagem):
                with open(caminho_imagem, "rb") as foto:
                    requests.post(
                        f"{base}/sendPhoto",
                        data={"chat_id": cfg.TELEGRAM_CHAT_ID},
                        files={"photo": foto},
                        timeout=30,
                    )
        except Exception as e:
            self.progress_callback(mensagem=f"[Telegram] Falha ao enviar aviso: {e}")

    def _salvar(self, df, ressonancias, pasta):
        timestamp = time.strftime("%Y-%m-%d_%H-%M")
        nome_base = (
            f"PULSADO_{self.mw_freq_center}MHz_{self.mw_freq_range}range_"
            f"{self.mw_freq_step}step_{self.naverage}averages_"
            f"pi{self.pi_pulse_time_ns:g}ns_{timestamp}"
        )
        caminho_csv = os.path.join(pasta, nome_base + ".csv")
        caminho_png = os.path.join(pasta, nome_base + ".png")
        df.to_csv(caminho_csv, index=False)

        fig = plt.figure(figsize=(16, 9))
        plt.plot(df.mw_freq, df.PL, linestyle="--", linewidth=1, color="black")
        plt.scatter(df.mw_freq, df.PL, facecolor="red", edgecolor="black", zorder=3)

        for r in ressonancias:
            plt.axvline(r["freq"], color="gray", linestyle=":", alpha=0.5)

        if ressonancias:
            melhor = ressonancias[0]
            plt.axvline(melhor["freq"], color="blue", linewidth=2, alpha=0.7)
            plt.annotate(
                f"{melhor['freq']:.2f} MHz\ncontraste {melhor['contraste'] * 100:.1f}%",
                xy=(melhor["freq"], melhor["valor"]),
                xytext=(10, -30),
                textcoords="offset points",
                fontsize=12,
                color="blue",
                arrowprops=dict(arrowstyle="->", color="blue"),
            )

        plt.xlabel("Frequência de MW (MHz)", fontsize=14)
        plt.ylabel("PL(MW on) / PL(MW off), normalizado", fontsize=14)
        plt.title("ODMR pulsado", fontsize=16)
        plt.tight_layout()
        plt.savefig(caminho_png)

        if ressonancias:
            melhor = ressonancias[0]
            msg = (
                f"🚀 ODMR pulsado de {time.strftime('%H:%M')} concluído! "
                f"Ressonância de maior contraste: {melhor['freq']:.2f} MHz "
                f"(contraste {melhor['contraste'] * 100:.1f}%)"
            )
        else:
            msg = f"🚀 ODMR pulsado de {time.strftime('%H:%M')} concluído (nenhum vale claro encontrado)."
        self._aviso_telegram(msg, caminho_png)

        return fig, caminho_png

    # ------------------------------------------------------------------ #
    # EXECUÇÃO
    # ------------------------------------------------------------------ #

    def executar(self):
        resultado = {"sucesso": False}

        try:
            pulsos_on, dur_ms = self._montar_sequencia("mw_on")
            pulsos_off, _ = self._montar_sequencia("mw_off")
            pulsos_back = self._montar_sequencia("back")[0] if self.usar_background else None

            n_freq = len(self.mw_freqs)
            n_sequencias = 3 if self.usar_background else 2
            tempo_total_s = self.naverage * n_freq * (n_sequencias * dur_ms / 1000.0 + TEMPO_ESTABILIZACAO_MW_S)
            self.progress_callback(
                mensagem=(
                    f"Cada leitura dura {dur_ms:.1f} ms. "
                    f"Tempo estimado: {tempo_total_s / 60:.1f} min."))

            self._iniciar_spincore()
            self._conectar_mw()
            self._definir_potencia_mw(self.mw_power)

            shutter_ms = dur_ms
            self._iniciar_camera(shutter_ms)
            self._aquecer_camera(pulsos_off)

            pasta = self._criar_pasta_saida()

            soma_on = np.zeros(n_freq)
            soma_off = np.zeros(n_freq)
            soma_back = np.zeros(n_freq)

            for media in range(self.naverage):

                if self.cancelado:
                    break

                for idx, f in enumerate(self.mw_freqs):

                    if self.cancelado:
                        break

                    self._definir_frequencia_mw(f)

                    time.sleep(TEMPO_ESTABILIZACAO_MW_S)

                    soma_on[idx] += self._medir(pulsos_on)
                    soma_off[idx] += self._medir(pulsos_off)
                    if pulsos_back is not None:
                        soma_back[idx] += self._medir(pulsos_back)

                    self.progress_callback(
                        progresso=(media * n_freq + idx + 1) / (self.naverage * n_freq),
                        mensagem=f"Média {media + 1}/{self.naverage} — {f:.2f} MHz",
                    )

                if self.cancelado:
                    break

            if self.cancelado:
                resultado["mensagem"] = "ODMR pulsado cancelado pelo usuário."
                return resultado

            df = self._calcular_espectro(soma_on, soma_off, soma_back)
            ressonancias = analise.detectar_ressonancias(df.mw_freq.values, df.PL.values)
            fig, caminho_png = self._salvar(df, ressonancias, pasta)

            resultado.update({
                "sucesso": True,
                "df": df,
                "ressonancias": ressonancias,
                "melhor_ressonancia": ressonancias[0] if ressonancias else None,
                "figura": fig,
                "caminho_grafico": caminho_png,
            })
            return resultado

        except Exception as e:
            resultado["mensagem"] = str(e)
            self.progress_callback(mensagem=f"Erro: {e}")
            return resultado

        finally:
            # Mesma ordem de segurança do ODMR CW: primeiro a MW, depois a câmera,
            # a placa e por último o VISA.
            self._parar_mw()
            self._parar_camera()
            self._parar_spincore()


# ---------------------------------------------------------------------- #
# Execução direta pelo terminal, para testar sem o painel
# ---------------------------------------------------------------------- #

if __name__ == "__main__":
    from tqdm import tqdm

    barra = tqdm(total=100, desc="ODMR pulsado", unit="%")

    def mostrar(progresso=None, mensagem=""):
        if progresso is not None:
            barra.update(max(0, int(progresso * 100) - barra.n))
            barra.set_postfix_str(mensagem, refresh=False)
        elif mensagem:
            barra.write(mensagem)

    medida = ODMRPulsado(
        mw_freq_center=3012.0,
        mw_freq_range=30.0,       # varre 3012 ± 30 MHz
        mw_freq_step=0.5,
        naverage=20,
        mw_power=-10.0,           # em dBm, nunca acima de 0
        pi_pulse_time_ns=170.0,   # o pi calibrado no Rabi, na mesma potência
        measure_loops=50,
        laser_time_us=600.0,
        dynamic_shutter=True,
        usar_background=False,
        progress_callback=mostrar,
    )

    resultado = medida.executar()
    barra.close()

    if resultado["sucesso"]:
        print("Ressonâncias (maior contraste primeiro):")
        for r in resultado["ressonancias"]:
            print(f"  {r['freq']:.2f} MHz   contraste {r['contraste'] * 100:.1f}%")
    else:
        print("Falhou:", resultado.get("mensagem"))

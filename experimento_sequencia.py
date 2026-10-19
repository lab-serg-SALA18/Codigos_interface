# -*- coding: utf-8 -*-
"""
Classe genérica que substitui o InicializadorSequencias.py original.

O arquivo antigo só sabia "ser o Rabi" — o nome da classe e das pastas eram
fixos em "Rabi" mesmo quando SEQUENCY era T1, HAN etc. no
ParametrosSequencias.py. Aqui a sequência é um parâmetro do construtor,
então a mesma classe atende qualquer experimento pulsado (RABI, T1, HAN,
RAM, CPMG2, CPMG4, CPMG8, ESEEM); é a interface gráfica que decide qual
rodar e com quais parâmetros.

Roda em Python 3.6 (mesmo ambiente da câmera). Conversa com o NVserver.py
(Python 3.11) por socket, exatamente como o código original — quem decide
o que vai em cada pulso continua sendo o servidor (GeradorSequencias.py),
com base no ParametrosSequencias.py que a interface escreveu antes de o
servidor subir (ver servidor_manager.py).

Principais diferenças em relação ao InicializadorSequencias.py original:
  - generalizado para qualquer SEQUENCY, não só Rabi (nome da classe, pastas,
    título do gráfico, etc. usam o nome da sequência);
  - habilita o trigger externo da câmera (habilitar_trigger_camera);
  - ajuste automático (T1/T2/T2*/Rabi/ESEEM) rodado no fim de processar_dados;
  - progress_callback para a interface gráfica acompanhar o andamento;
  - desliga a potência de MW ao final (ou em caso de erro), por segurança;
  - salvar imagem de cada frame individual agora é opcional
    (ver SALVAR_IMAGENS_INDIVIDUAIS em configuracoes_lab.py).
"""

import os
import socket
import json
import time
import csv
import numpy as np
import matplotlib.pyplot as plt
import h5py
import pyvisa as visa
import PyCapture2
import requests
from tqdm import tqdm

import configuracoes_lab as cfg
import analise_automatica as analise

import sys
import io
try:
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass
class ExperimentoSequencia:
    def __init__(
        self,
        sequencia,
        frequency,
        time_array,
        mw_power,
        average,
        infos,
        dynamic_shutter=True,
        backend_photo_on=False,
        plotar_fit=True,
        progress_callback=None,
    ):
        self.sequencia_nome = sequencia
        self.frequency = frequency
        self.time_array = list(time_array)
        self.mw_power = mw_power
        self.average = average
        self.infos = infos
        self.hora = ""

        self.camera = None
        self.instrument = None

        self.backend_photo_on = backend_photo_on
        self.dynamic_shutter = dynamic_shutter
        self.plotar_fit = plotar_fit

        self.progress_callback = progress_callback or (lambda **kwargs: None)
        self.cancelado = False

        self.pasta_dados_medida = None
        self.pasta_graficos = None
        self.sock_qiskit = None

    # _________________________________________________________ #
    # ---------------- HARDWARE DO LABORATÓRIO ---------------- #
    def iniciar_camera(self):
        self.camera = PyCapture2.Camera()
        bus = PyCapture2.BusManager()
        self.camera.connect(bus.getCameraFromIndex(0))
        self.camera.setConfiguration(grabTimeout=5000)
        self.habilitar_trigger_camera()
        self.camera.startCapture()

    def habilitar_trigger_camera(self):
        """
        Coloca a câmera em modo de trigger externo (hardware trigger), com
        polaridade ALTA, para que a exposição seja disparada pelo próprio
        pulso 'camera_on' (bit 12) da sequência, em vez de depender só do
        ajuste manual de shutter (self.dynamic_shutter).

        mode=0 e source=0 são o padrão para câmeras Point Grey / FLIR com
        um único pino de trigger (GPIO 0). Se a exposição não sincronizar
        como esperado, confira "source" no manual da sua câmera (pode ser
        um GPIO diferente dependendo de como ela está cabeada à SpinCore).
        """
        trigger_mode = self.camera.getTriggerMode()
        trigger_mode.onOff = True
        trigger_mode.polarity = 1   # 1 = ativo em nível/borda ALTA
        trigger_mode.mode = 0       # modo padrão de trigger externo
        trigger_mode.source = 0     # fonte de trigger externo (GPIO 0)
        trigger_mode.parameter = 0
        self.camera.setTriggerMode(trigger_mode)

    def conectar_gerador_mw(self):
        rm = visa.ResourceManager()
        self.instrument = rm.open_resource(cfg.MW_RESOURCE_NAME)

    def definir_frequencia_potencia_mw(self, frequency, power):
        self.instrument.write(f"FREQ {frequency} MHz")
        self.instrument.write(f"AMPR {power}dBm")

    def definir_shutter(self, shutter_time_ms):
        shutter = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.SHUTTER)
        shutter.autoManualMode = False
        shutter.onOff = True
        shutter.absControl = True
        shutter.absValue = shutter_time_ms
        self.camera.setProperty(shutter)

    def definir_ganho(self, gain_value=0):
        gain = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.GAIN)
        gain.autoManualMode = False
        gain.onOff = True
        gain.absControl = True
        gain.absValue = gain_value
        self.camera.setProperty(gain)

    # _________________________________________________________ #
    # ----------------------- DADOS --------------------------- #
    def criar_diretorio(self):
        data_hoje = time.strftime("%d_%m_%Y")
        destino = time.strftime("%d_%m_%Y--%H_%M")
        self.hora = destino

        pasta_dados = os.path.join(
            cfg.PASTA_DADOS_BASE, 
            self.sequencia_nome, 
            "Dados", 
            data_hoje
            )
        pasta_graficos = os.path.join(
            cfg.PASTA_DADOS_BASE, 
            self.sequencia_nome, 
            "Graficos", 
            data_hoje
            )
        self.pasta_dados_medida = os.path.join(pasta_dados, destino)
        self.pasta_graficos = pasta_graficos

        os.makedirs(self.pasta_dados_medida, exist_ok=True)
        os.makedirs(pasta_graficos, exist_ok=True)

    def salvar_arquivo_h5(self, data_mw_on, data_background, data_mw_off):
        """Salva os três H5F e gera automaticamente o CSV dos pontos."""
        with h5py.File(os.path.join(self.pasta_dados_medida, "Cam_MW_on.h5f"), "w") as arq:
            arq.create_dataset("dataset", data=data_mw_on, dtype="Float64", compression="gzip")

        with h5py.File(os.path.join(self.pasta_dados_medida, "Cam_background.h5f"), "w") as arq:
            arq.create_dataset("dataset", data=data_background, dtype="Float64", compression="gzip")

        with h5py.File(os.path.join(self.pasta_dados_medida, "Cam_MW_off.h5f"), "w") as arq:
            arq.create_dataset("dataset", data=data_mw_off, dtype="Float64", compression="gzip")

        try:
            caminho_csv = self.gerar_csv_pontos()
        except Exception as exc:
            self.progress_callback(mensagem="AVISO: os H5F foram salvos, mas o CSV não pôde ser gerado: {}".format(exc))

    def _somar_dataset_h5(self, nome_arquivo):
        """Lê o dataset 3D de um H5F e soma os pixels para cada ponto temporal."""
        caminho = os.path.join(self.pasta_dados_medida, nome_arquivo)
        with h5py.File(caminho, "r") as arq:
            if "dataset" not in arq:
                raise KeyError("O arquivo {} não contém o dataset 'dataset'.".format(nome_arquivo))
            dados = np.asarray(arq["dataset"], dtype=np.float64)

        return np.sum(dados, axis=(0, 1), dtype=np.float64)

    @staticmethod
    def _normalizar_minmax(valores):
        valores = np.asarray(valores, dtype=np.float64)
        resultado = np.full(valores.shape, np.nan, dtype=np.float64)
        validos = np.isfinite(valores)

        if not np.any(validos):
            return resultado

        minimo = float(np.min(valores[validos]))
        amplitude = float(np.max(valores[validos]) - minimo)
        if not np.isfinite(amplitude) or amplitude <= 0:
            return resultado

        resultado[validos] = (valores[validos] - minimo) / amplitude
        return resultado


    def gerar_csv_pontos(self):
        """
        Gera pontos_experimentais.csv a partir dos H5F acabados de salvar.
        O eixo temporal utiliza self.time_array, evitando passo temporal fixo.
        """
        soma_on = self._somar_dataset_h5("Cam_MW_on.h5f")
        soma_off = self._somar_dataset_h5("Cam_MW_off.h5f")

        if len(soma_on) != len(self.time_array) or len(soma_off) != len(self.time_array):
            raise ValueError("Número de pontos dos H5F não corresponde a self.time_array.")

        if self.backend_photo_on:
            soma_background = self._somar_dataset_h5("Cam_background.h5f")
            if len(soma_background) != len(self.time_array):
                raise ValueError("Número de pontos do background não corresponde a self.time_array.")
            numerador = soma_on - soma_background
            denominador = soma_off - soma_background
        else:
            soma_background = np.full_like(soma_on, np.nan, dtype=np.float64)
            numerador = soma_on
            denominador = soma_off

        razao = np.full_like(soma_on, np.nan, dtype=np.float64)
        validos = (
            np.isfinite(numerador)
            & np.isfinite(denominador)
            & (np.abs(denominador) > np.finfo(np.float64).eps))
        razao[validos] = numerador[validos] / denominador[validos]
        sinal_normalizado = self._normalizar_minmax(razao)

        tempos = np.asarray(self.time_array, dtype=np.float64)
        unidade_tempo = "us" if self.sequencia_nome.upper() == "T1" else "ns"
        coluna_tempo = "tempo_us" if unidade_tempo == "us" else "tempo_ns"
        caminho_csv = os.path.join(self.pasta_dados_medida, "pontos_experimentais.csv")


        colunas = [
            "indice_ponto", coluna_tempo,
            "soma_pixels_MW_on", "soma_pixels_MW_off", "soma_pixels_background",
            "razao_sinal", "sinal_normalizado", "sinal_normalizado_0_1",
        ]

        with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as arquivo_csv:
            escritor = csv.writer(arquivo_csv)
            escritor.writerow(colunas)
            for i, tempo in enumerate(tempos):
                razao_i = razao[i]
                normalizado_i = sinal_normalizado[i]
                escritor.writerow([
                    i,
                    "{:.12g}".format(tempo),
                    "{:.12g}".format(soma_on[i]),
                    "{:.12g}".format(soma_off[i]),
                    ("{:.12g}".format(soma_background[i])
                     if self.backend_photo_on and np.isfinite(soma_background[i]) else ""),
                    "{:.12g}".format(razao_i) if np.isfinite(razao_i) else "",
                    "{:.12g}".format(normalizado_i) if np.isfinite(normalizado_i) else "",
                    "{:.12g}".format(normalizado_i) if np.isfinite(normalizado_i) else "",])

        return caminho_csv

    # _________________________________________________________ #
    # ---------------- PROCESSAMENTO & AQUISIÇÃO -------------- #
    def gerar_dados_imagens(self):
        data_mw_on, data_mw_off, data_background = self._iniciar_dados()
        tipos_foto = ["back", "mw_off", "mw_on"] if self.backend_photo_on else ["mw_off", "mw_on"]
        total_passos = max(self.average * len(self.time_array) * len(tipos_foto), 1)
        barra = tqdm(
            total=total_passos,
            desc="Aquisição {}".format(self.sequencia_nome),
            unit="frame",
            mininterval=1,
            leave=False,
        )

        try:
            for i in range(1, self.average + 1):
                if self.cancelado:
                    break
                for mw_counter, mw_value in enumerate(self.time_array):
                    if self.cancelado:
                        break
                    for tipo in tipos_foto:
                        if self.cancelado:
                            break
                        self.executar_sequencia_pulsos(mw_value, tipo)

                        
                        raw_image_data = self.camera.retrieveBuffer()
                        matriz = self.converter_imagem_para_matriz(raw_image_data)

                        if tipo == "mw_on":
                            data_mw_on[:, :, mw_counter] += matriz
                        elif tipo == "mw_off":
                            data_mw_off[:, :, mw_counter] += matriz
                        elif tipo == "back":
                            data_background[:, :, mw_counter] += matriz

                        if cfg.SALVAR_IMAGENS_INDIVIDUAIS:
                            nome_frame = f"{mw_value}_{tipo}_{i}.png"
                            raw_image_data.save(nome_frame.encode("utf-8"), PyCapture2.IMAGE_FILE_FORMAT.PNG)

                        barra.update(1)
                    self._publicar_progresso_tqdm(barra)
        finally:
            barra.close()

        self.salvar_arquivo_h5(data_mw_on, data_background, data_mw_off)
        return data_mw_on / self.average, data_mw_off / self.average, data_background / self.average

    def _publicar_progresso_tqdm(self, barra):
        """Repasse o progresso e a ETA calculada pelo tqdm para a interface."""
        dados = barra.format_dict
        taxa = dados.get("rate")
        restante = None
        if taxa and taxa > 0 and barra.total is not None:
            restante = max(0.0, (barra.total - barra.n) / taxa)
        self.progress_callback(
            progresso=barra.n / float(barra.total),
            tempo_decorrido=float(dados.get("elapsed", 0.0)),
            tempo_restante=restante,
        )

    def _iniciar_dados(self):
        # Dispara uma sequência inicial para enviar o trigger do hardware para a câmera
        self.executar_sequencia_pulsos(self.time_array[0], "mw_off")

        raw = None
        for _ in range(10):
            if self.cancelado:
                raise RuntimeError("Medida cancelada durante a inicialização da câmera.")
            
            try:
                raw = self.camera.retrieveBuffer()
                break
            except PyCapture2.Fc2error:
                self.progress_callback(mensagem="Aguardando câmera iniciar...")
                time.sleep(0.5)

        if raw is None:
            raise RuntimeError("Não foi possível obter o primeiro frame da câmera.")

        width = raw.getRows() - 2
        height = raw.getCols() - 2
        formato = (height, width, len(self.time_array))
        return np.zeros(formato), np.zeros(formato), np.zeros(formato)

    def executar_sequencia_pulsos(self, mw_value, tipo):
        comando = {
            "acao": "circuit_sequence",
            "mw_value": float(mw_value),
            "typePhoto": tipo,
        }
        self.sock_qiskit.sendall(json.dumps(comando).encode("utf-8"))
        resposta = self.sock_qiskit.recv(4096).decode("utf-8")
        resultado = json.loads(resposta)

        if resultado["status"] == "ok":
            if self.dynamic_shutter:
                self.definir_shutter(resultado["circuit_time"])
        else:
            erro_bruto = str(resultado.get('msg', 'Erro desconhecido'))
            erro_limpo = erro_bruto.encode('cp1252', errors='replace').decode('cp1252')
            raise RuntimeError(f"Erro do servidor Qiskit: {erro_limpo}")

    def converter_imagem_para_matriz(self, image):
        width = image.getRows()
        height = image.getCols()
        dados = np.frombuffer(image.getData(), dtype=np.uint16)
        matriz = dados.reshape((height, width))
        return matriz[1:-1, 1:-1]

    def processar_dados(self, array_mw_on, array_mw_off, array_background):
        n = len(self.time_array)
        soma_on = np.array([np.sum(array_mw_on[:, :, k]) for k in range(n)])
        soma_off = np.array([np.sum(array_mw_off[:, :, k]) for k in range(n)])
        soma_back = np.array([np.sum(array_background[:, :, k]) for k in range(n)])

        sinal = (soma_on - soma_back) / (soma_off - soma_back)
        sinal = sinal - min(sinal)
        sinal = sinal / max(sinal)

        return sinal, soma_on, soma_off

    def enviar_aviso_telegram(self, mensagem, caminho_imagem=None):
        if not cfg.TELEGRAM_ATIVO_PADRAO:
            return
        try:
            url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": mensagem}, timeout=10)

            if caminho_imagem and os.path.isfile(caminho_imagem):
                url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendPhoto"
                with open(caminho_imagem, "rb") as foto:
                    requests.post(
                        url, data={"chat_id": cfg.TELEGRAM_CHAT_ID}, files={"photo": foto}, timeout=30
                    )
        except Exception as e:
            self.progress_callback(mensagem=f"[Telegram] Falha ao enviar aviso: {e}")

    def plotar_e_salvar(self, sinal, ajuste, tempos=None):
        if tempos is None:
            tempos = self.time_array
        unidade_tempo = cfg.EXPERIMENTOS.get(self.sequencia_nome, {}).get("unidade_tempo", "")

        fig = plt.figure(figsize=(16, 9))
        plt.plot(tempos, sinal, "o-k", label="Dados")

        if self.plotar_fit and ajuste.get("sucesso") and ajuste.get("curva_fit"):
            t_fit, y_fit = ajuste["curva_fit"]
            plt.plot(t_fit, y_fit, "-r", linewidth=2, label="Ajuste")

        plt.tick_params(direction="out", length=5, width=2, colors="black", labelsize=16)
        plt.xlabel(f"Tempo ({unidade_tempo})" if unidade_tempo else "Tempo", fontsize=18)
        plt.ylabel("Fotoluminescência (normalizada)", fontsize=18)
        plt.grid(alpha=0.3)
        plt.legend(fontsize=14)

        titulo = self.sequencia_nome
        if self.plotar_fit and ajuste.get("sucesso"):
            valor = ajuste["valor"]
            unidade = ajuste.get("unidade", "")
            incerteza = ajuste.get("incerteza", float("nan"))
            if np.isfinite(incerteza):
                titulo += f" — {ajuste['rotulo']} = {valor:.3g} ± {incerteza:.2g} {unidade}"
            else:
                titulo += f" — {ajuste['rotulo']} = {valor:.3g} {unidade}"
            if "t2_star_rabi_ns" in ajuste:
                t2 = ajuste["t2_star_rabi_ns"]
                erro_t2 = ajuste.get("t2_star_rabi_incerteza_ns", float("nan"))
                if np.isfinite(erro_t2):
                    titulo += f" | T2* (Rabi) = {t2:.3g} ± {erro_t2:.2g} ns"
                else:
                    titulo += f" | T2* (Rabi) = {t2:.3g} ns"
        plt.title(titulo, fontsize=16)

        nome_arquivo = f"{self.infos}_{self.hora}.png"
        caminho_grafico = os.path.join(self.pasta_graficos, nome_arquivo)
        caminho_dados = os.path.join(self.pasta_dados_medida, nome_arquivo)
        plt.savefig(caminho_grafico, format="png", bbox_inches="tight")
        plt.savefig(caminho_dados, format="png", bbox_inches="tight")

        if ajuste.get("sucesso"):
            msg = (
                f"🚀 Medida {self.sequencia_nome} de {time.strftime('%H:%M')} concluída!\n"
                f"{ajuste['rotulo']} = {ajuste['valor']:.4g} {ajuste.get('unidade', '')}"
            )
            if "t2_star_rabi_ns" in ajuste:
                msg += f"\nT2* (Rabi) = {ajuste['t2_star_rabi_ns']:.4g} ns"
        else:
            msg = f"🚀 Medida {self.sequencia_nome} de {time.strftime('%H:%M')} concluída (sem ajuste automático)."
        self.enviar_aviso_telegram(msg, caminho_dados)

        return fig, caminho_dados

    # _________________________________________________________ #
    # ------------------- FUNÇÃO PRINCIPAL -------------------- #
    def executar(self):
        resultado = {"sucesso": False}
        try:
            self.criar_diretorio()

            self.sock_qiskit = socket.create_connection(("127.0.0.1", cfg.PORTA_QISKIT), timeout=10)
            self.sock_qiskit.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            self.iniciar_camera()
            #self.definir_ganho(14)
            self.definir_ganho(-2.815)
            self.definir_shutter(20)

            self.conectar_gerador_mw()
            if self.mw_power > 10:
                self.progress_callback(mensagem="Atenção: potência de MW muito alta (>10 dBm). Definindo para 10 dBm por segurança.")
                self.mw_power = 10
            self.definir_frequencia_potencia_mw(self.frequency, self.mw_power)

            data_mw_on, data_mw_off, data_background = self.gerar_dados_imagens()

            if self.cancelado:
                resultado["mensagem"] = "Medida cancelada pelo usuário."
                return resultado

            sinal, _, _ = self.processar_dados(data_mw_on, data_mw_off, data_background)
            ajuste = analise.ajuste_automatico(self.sequencia_nome, self.time_array, sinal)

            # Correção do atraso observado no Rabi
            tempos_ajuste = np.asarray(self.time_array, dtype=float).copy()
            sinal_ajuste = np.asarray(sinal, dtype=float).copy()

            ajuste = analise.ajuste_automatico(
                self.sequencia_nome,
                tempos_ajuste,
                sinal_ajuste)

            # Guarda os tempos corrigidos para o gráfico
            tempos_grafico = tempos_ajuste
            sinal_grafico = sinal_ajuste

            fig, caminho_grafico = self.plotar_e_salvar(sinal_grafico, ajuste, tempos=tempos_grafico)

            resultado.update({
                "sucesso": True,
                "sinal": sinal,
                "tempos": self.time_array,
                "ajuste": ajuste,
                "figura": fig,
                "caminho_grafico": caminho_grafico,
            })
            return resultado


        except Exception as e:
            resultado["mensagem"] = str(repr(e))
            self.progress_callback(mensagem=f"Erro na execução: {repr(e)}")
            return resultado

        finally:
            if self.sock_qiskit is not None:
                try:
                    self.sock_qiskit.close()
                except Exception:
                    pass
            if self.instrument is not None:
                try:
                    self.definir_frequencia_potencia_mw(self.frequency, -100)
                except Exception:
                    pass
            if self.camera is not None:
                try:
                    trigger_mode = self.camera.getTriggerMode()
                    trigger_mode.onOff = False
                    trigger_mode.polarity = 0
                    self.camera.setTriggerMode(trigger_mode)
                except Exception:
                    pass
                try:
                    self.camera.stopCapture()
                except Exception:
                    pass
                try:
                    self.camera.disconnect()
                except Exception:
                    pass
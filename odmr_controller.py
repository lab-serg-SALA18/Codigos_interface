# -*- coding: utf-8 -*-
"""
Controlador de ODMR, adaptado do odmr.py original.

Principais adições em relação ao original:
  - ativar_laser_camera_continuo(): substitui o passo manual de abrir o
    software da SpinCore e carregar o 'ativador_camera.txt' antes de cada
    ODMR. Programa a mesma sequência (laser + chave de MW + trigger da
    câmera sempre em nível alto) diretamente por código, usando
    pb_inst_pbonly.
  - Depois da varredura, detecta automaticamente a(s) ressonância(s) e
    aponta a de maior contraste (o vale mais fundo do espectro), usando
    analise_automatica.detectar_ressonancias.
  - progress_callback para a interface gráfica acompanhar o andamento.
  - Liberação completa da SpinCore, câmera e MW mesmo em cancelamento ou erro.

Roda inteiramente em Python 3.6 (câmera antiga) — não depende do NVserver.py
nem do Qiskit, então não precisa do subprocesso do Python 3.11.
"""

import os
import time

import numpy as np
import pandas as pd
import pyvisa as visa
import PyCapture2
import matplotlib.pyplot as plt
import requests

from spinapi import (
    pb_init,
    pb_core_clock,
    pb_start_programming,
    pb_inst_pbonly,
    pb_stop_programming,
    pb_start,
    pb_stop,
    pb_close,
    pb_get_error,
    PULSE_PROGRAM,
    CONTINUE,
    BRANCH,
    ms,
)

import configuracoes_lab as cfg
import analise_automatica as analise


class ODMRController:
    def __init__(
        self,
        shutter_time_ms,
        mw_freq_center,
        mw_freq_range,
        mw_freq_step,
        naverage,
        mw_power,
        progress_callback=None,
    ):
        self.shutter_time = shutter_time_ms
        self.mw_freq_center = mw_freq_center
        self.mw_freq_range = mw_freq_range
        self.mw_freq_step = mw_freq_step
        self.naverage = int(naverage)
        self.mw_power = mw_power

        # Tempo para estabilização da freq. de MW:
        # delay do sintetizador (~8 ms) + shutter.
        self.settling_time = shutter_time_ms / 1e3 + 0.01

        self.mw_freqs = np.arange(
            mw_freq_center - mw_freq_range,
            mw_freq_center + mw_freq_range + mw_freq_step,
            mw_freq_step,
        )

        self.camera = None
        self.mw = None
        self.rm = None

        self.spincore_initialized = False
        self.cancelado = False

        self.progress_callback = progress_callback or (lambda **kwargs: None)

    # ---------------- HARDWARE ---------------- #

    def iniciar_spincore(self):
        if not self.spincore_initialized:
            if pb_init() != 0:
                raise RuntimeError(pb_get_error())

            pb_core_clock(300.0)
            self.spincore_initialized = True

    def parar_spincore(self):
        """
        Para qualquer programa em execução e libera a placa SpinCore.

        Essencial para que um ODMR cancelado não deixe a placa ocupada
        para a próxima experiência.
        """
        if not self.spincore_initialized:
            return

        try:
            pb_stop()
        except Exception:
            pass

        try:
            pb_close()
        except Exception:
            pass

        self.spincore_initialized = False

    def ativar_laser_camera_continuo(self):
        """
        Substitui o passo manual de rodar o ativador de laser: mantém laser,
        chave de MW e trigger da câmera em nível alto continuamente, que é
        o que o ODMR (medida CW / onda contínua) precisa.

        Equivalente a carregar o ativador_camera.txt no software da
        SpinCore antes de medir.
        """
        self.iniciar_spincore()

        pb_start_programming(PULSE_PROGRAM)

        inicio = pb_inst_pbonly(cfg.TUDO_LIGADO,CONTINUE,0,500 * ms,)

        pb_inst_pbonly(cfg.TUDO_LIGADO,BRANCH,inicio,500 * ms,)

        pb_stop_programming()
        pb_start()

    def conectar_mw(self):
        self.rm = visa.ResourceManager()
        self.mw = self.rm.open_resource(cfg.MW_RESOURCE_NAME)

    def parar_mw(self):
        """Desliga a potência e fecha o recurso VISA com segurança."""
        if self.mw is not None:
            try:
                self.definir_potencia_mw(-100)
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

    def definir_frequencia_mw(self, freq_mhz):
        self.mw.write(f"FREQ {freq_mhz} MHz")

    def definir_potencia_mw(self, power_dbm):
        if power_dbm > 0:
            raise ValueError("Potência de MW perigosa (>0 dBm) — abortado por segurança.")

        self.mw.write(f"AMPR {power_dbm}")

    def iniciar_camera(self):
        self.camera = PyCapture2.Camera()

        bus = PyCapture2.BusManager()
        self.camera.connect(bus.getCameraFromIndex(0))

        config = self.camera.getConfiguration()
        config.grabTimeout = 5000
        self.camera.setConfiguration(config)

        trigger_mode = self.camera.getTriggerMode()

        # ODMR = câmera em free-running.
        trigger_mode.onOff = False
        trigger_mode.polarity = 0

        self.camera.setTriggerMode(trigger_mode)

        # Shutter manual
        shutter_prop = self.camera.getProperty(PyCapture2.PROPERTY_TYPE.SHUTTER)

        shutter_prop.autoManualMode = False
        shutter_prop.absValue = float(self.shutter_time)

        self.camera.setProperty(shutter_prop)

        self.camera.startCapture()

    def parar_camera(self):
        """Desliga a captura e desconecta a câmera com segurança."""
        if self.camera is None:
            return

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

        self.camera = None

    # ---------------- PROCESSAMENTO ---------------- #

    def imagem_para_array(self, image):
        dados = np.frombuffer(image.getData(), dtype=np.uint16)

        h = image.getCols()
        w = image.getRows()

        img = dados.reshape((h, w))

        return img[1:-1, 1:-1]

    def medir_intensidade(self, image):
        return np.sum(self.imagem_para_array(image))

    # ---------------- AQUISIÇÃO ---------------- #

    def rodar_aquisicao(self):
        sinal_total = np.zeros_like(self.mw_freqs, dtype=float)

        self.iniciar_camera()

        n_freqs = len(self.mw_freqs)

        try:
            for media in range(self.naverage):

                if self.cancelado:
                    break

                for idx, f in enumerate(self.mw_freqs):

                    if self.cancelado:
                        break

                    self.definir_frequencia_mw(f)

                    time.sleep(self.settling_time)

                    img = self.camera.retrieveBuffer()

                    sinal_total[idx] += self.medir_intensidade(img)

                    self.progress_callback(
                        progresso=(media * n_freqs + idx + 1) / (self.naverage * n_freqs),
                        mensagem=(f"Média {media + 1}/{self.naverage} "f"— {f:.2f} MHz"),)

        finally:
            # Mantém esta proteção local.
            self.parar_camera()

        if self.cancelado:
            return pd.DataFrame({"mw_freq": self.mw_freqs,"PL": np.full(len(self.mw_freqs),np.nan,),})

        sinal_medio = sinal_total / self.naverage

        sinal_medio = sinal_medio / max(sinal_medio)

        return pd.DataFrame({"mw_freq": self.mw_freqs,"PL": sinal_medio,})

    # ---------------- IO ---------------- #

    def criar_pasta_saida(self):
        nome_pasta = time.strftime("%d_%m_%Y")

        caminho = os.path.join(cfg.PASTA_DADOS_BASE,"ODMR","Dados",nome_pasta,)

        os.makedirs(caminho, exist_ok=True)

        return caminho

    def enviar_aviso_telegram(self, mensagem, caminho_imagem=None):
        if not cfg.TELEGRAM_ATIVO_PADRAO:
            return

        try:
            url = (f"https://api.telegram.org/bot"f"{cfg.TELEGRAM_TOKEN}/sendMessage")

            requests.post(
                url,
                json={"chat_id": cfg.TELEGRAM_CHAT_ID,"text": mensagem,},timeout=10,)

            if caminho_imagem and os.path.isfile(caminho_imagem):
                url = (f"https://api.telegram.org/bot"f"{cfg.TELEGRAM_TOKEN}/sendPhoto")

                with open(caminho_imagem, "rb") as foto:
                    requests.post(url,data={"chat_id": cfg.TELEGRAM_CHAT_ID},files={"photo": foto},timeout=30,)

        except Exception as e:
            self.progress_callback(mensagem=f"[Telegram] Falha ao enviar aviso: {e}")

    def salvar_dados(self, df, ressonancias, pasta):
        timestamp = time.strftime("%Y-%m-%d_%H-%M")

        nome_base = (
            f"{self.mw_freq_center}MHz_"
            f"{self.mw_freq_range}range_"
            f"{self.mw_freq_step}steps_"
            f"{self.naverage}averages_"
            f"{self.mw_power}dB_"
            f"{timestamp}"
        )

        caminho_csv = os.path.join(pasta,nome_base + ".csv",)

        caminho_png = os.path.join(pasta,nome_base + ".png",)

        df.to_csv(caminho_csv,index=False,)

        fig = plt.figure(figsize=(16, 9))

        plt.plot(df.mw_freq,df.PL,linestyle="--",linewidth=1,color="black",)

        plt.scatter(df.mw_freq,df.PL,facecolor="red",edgecolor="black",zorder=3,)

        for r in ressonancias:
            plt.axvline(r["freq"],color="gray",linestyle=":",alpha=0.5,)

        if ressonancias:
            melhor = ressonancias[0]

            plt.axvline(melhor["freq"],color="blue",linestyle="-",linewidth=2,alpha=0.7,)

            plt.annotate(
                f"{melhor['freq']:.2f} MHz\n"
                f"contraste {melhor['contraste'] * 100:.1f}%",

                xy=(melhor["freq"],melhor["valor"],),

                xytext=(10,-30,),

                textcoords="offset points",

                fontsize=12,
                color="blue",

                arrowprops=dict(arrowstyle="->",color="blue",),)

        plt.xlabel("Frequência de MW (MHz)",fontsize=14,)

        plt.ylabel("PL total (u.a.)",fontsize=14,)

        plt.title("ODMR",fontsize=16,)

        plt.tight_layout()
        plt.savefig(caminho_png)

        if ressonancias:
            melhor = ressonancias[0]

            msg = (f"🚀 ODMR de {time.strftime('%H:%M')} concluído! Ressonância de maior contraste: "
                   f"{melhor['freq']:.2f} MHz (contraste {melhor['contraste'] * 100:.1f}%)")

        else:
            msg = (f"🚀 ODMR de {time.strftime('%H:%M')} concluído (nenhum vale claro encontrado).")

        self.enviar_aviso_telegram(msg,caminho_png)

        return fig, caminho_png

    # ---------------- PIPELINE ---------------- #

    def executar(self):
        resultado = {"sucesso": False}

        try:
            self.conectar_mw()

            self.definir_potencia_mw(self.mw_power)

            self.ativar_laser_camera_continuo()

            pasta = self.criar_pasta_saida()

            df = self.rodar_aquisicao()

            if self.cancelado:
                resultado["mensagem"] = ("ODMR cancelado pelo usuário.")
                return resultado

            ressonancias = analise.detectar_ressonancias(df.mw_freq.values,df.PL.values,)

            fig, caminho_png = self.salvar_dados(df,ressonancias,pasta,)

            resultado.update({"sucesso": True,"df": df,"ressonancias": ressonancias,
                "melhor_ressonancia": (ressonancias[0]if ressonancias else None),
                "figura": fig,"caminho_grafico": caminho_png,})

            return resultado

        except Exception as e:
            resultado["mensagem"] = str(e)

            self.progress_callback(mensagem=f"Erro: {e}")

            return resultado

        finally:
            self.parar_mw()

            self.parar_camera()

            self.parar_spincore()
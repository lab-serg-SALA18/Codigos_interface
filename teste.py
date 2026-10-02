# -*- coding: utf-8 -*-
"""
ANÁLISE DE T2* DO RABI — manual + automática (com ajuste de n)

Lê:
    Cam_MW_on.h5f
    Cam_MW_off.h5f
    Cam_background.h5f

e calcula:
    S(t) = (ON - BACKGROUND) / (OFF - BACKGROUND), normalizado (min->0, max->1)

Modelo:
    y(t) = A * exp(-(t/T2*)^n) * cos(2*pi*f*t + phi) + C
"""

from pathlib import Path

import h5py
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import TextBox, Button
from scipy.optimize import curve_fit


# ============================================================
# CONFIGURAÇÃO DA MEDIDA
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

FILE_ON = BASE_DIR / "Cam_MW_on.h5f"
FILE_OFF = BASE_DIR / "Cam_MW_off.h5f"
FILE_BACKGROUND = BASE_DIR / "Cam_background.h5f"

TIME_START_NS = 0.0
TIME_STEP_NS = 30.0
TIME_STOP_NS = None  # None = calcular pelo número de pontos

USE_BACKGROUND = True

FIT_START_NS = 0.0
FIT_STOP_NS = None  # None = até o último ponto


# ============================================================
# LEITURA E PROCESSAMENTO
# ============================================================

def carregar_h5(caminho):
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError("Arquivo não encontrado: {}".format(caminho))
    with h5py.File(caminho, "r") as arquivo:
        if "dataset" not in arquivo:
            raise KeyError("{} não contém o dataset 'dataset'.".format(caminho))
        return np.asarray(arquivo["dataset"], dtype=float)


def calcular_sinal(on, off, background):
    if on.shape != off.shape or on.shape != background.shape:
        raise ValueError(
            "Os três arquivos precisam ter a mesma dimensão.\n"
            "ON   = {}\nOFF  = {}\nBACK = {}".format(on.shape, off.shape, background.shape)
        )

    soma_on = np.sum(on, axis=(0, 1))
    soma_off = np.sum(off, axis=(0, 1))
    soma_back = np.sum(background, axis=(0, 1))

    if USE_BACKGROUND:
        numerador = soma_on - soma_back
        denominador = soma_off - soma_back
    else:
        numerador = soma_on
        denominador = soma_off

    eps = 1e-15
    if np.any(np.abs(denominador) <= eps):
        raise ValueError("O denominador ficou próximo de zero em alguns pontos.")

    sinal = numerador / denominador
    if not np.all(np.isfinite(sinal)):
        raise ValueError("O sinal contém NaN ou infinito.")

    sinal = sinal - np.min(sinal)
    maximo = np.max(sinal)
    if maximo <= 0:
        raise ValueError("O sinal não possui variação após a normalização.")

    return sinal / maximo


# ============================================================
# MODELO
# ============================================================

def modelo_rabi(t, A, T2s, f_mhz, phi, C, n):
    """t em ns, T2s em ns, f_mhz em MHz, phi em rad, n adimensional."""
    f_ciclos_por_ns = f_mhz / 1000.0
    return A * np.exp(-((t / T2s) ** n)) * np.cos(2.0 * np.pi * f_ciclos_por_ns * t + phi) + C


def calcular_r2(y, yfit):
    ss_res = np.sum((y - yfit) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


# ============================================================
# LEITURA DOS DADOS
# ============================================================

print("Lendo os três arquivos...")
dados_on = carregar_h5(FILE_ON)
dados_off = carregar_h5(FILE_OFF)
dados_back = carregar_h5(FILE_BACKGROUND)

sinal = calcular_sinal(dados_on, dados_off, dados_back)
n_pts = len(sinal)

TIME_STOP_CALCULADO = TIME_STOP_NS if TIME_STOP_NS is not None else TIME_START_NS + (n_pts - 1) * TIME_STEP_NS
tempos = np.linspace(TIME_START_NS, TIME_STOP_CALCULADO, n_pts)

# ============================================================
# CHUTES INICIAIS E LIMITES
# ============================================================

A_INICIAL = 0.5
T2_INICIAL = 250.0
F_INICIAL = 3.2
PHI_INICIAL = 0.0
C_INICIAL = 0.5
N_INICIAL = 1.0  # Exponencial simples de partida

# Limites para [A, T2s, f_mhz, phi, C, n]
LIMITES_INFERIORES = [0.0, 1.0, 0.05, -np.pi, -1.0, 0.1]
LIMITES_SUPERIORES = [3.0, 1.0e6, 200.0, np.pi, 2.0, 5.0]


# ============================================================
# FIGURA E INTERFACE
# ============================================================

fig = plt.figure(figsize=(15, 9))
ax = fig.add_axes([0.07, 0.33, 0.60, 0.62])

linha_dados, = ax.plot(tempos, sinal, "ko", markersize=4, label="Dados")

tfit = np.linspace(FIT_START_NS, FIT_STOP_NS if FIT_STOP_NS is not None else tempos[-1], 1200)
linha_fit, = ax.plot(
    tfit,
    modelo_rabi(tfit, A_INICIAL, T2_INICIAL, F_INICIAL, PHI_INICIAL, C_INICIAL, N_INICIAL),
    "r-", linewidth=2, label="Fit",
)

ax.set_xlabel("Tempo (ns)", fontsize=14)
ax.set_ylabel("Fotoluminescência normalizada", fontsize=14)
ax.grid(alpha=0.3)
ax.legend(fontsize=12)


def criar_caixa(y, nome, valor):
    eixo = fig.add_axes([0.74, y, 0.20, 0.04])
    return TextBox(eixo, nome, initial=str(valor))


caixa_A = criar_caixa(0.88, "A", A_INICIAL)
caixa_T2 = criar_caixa(0.82, "T2* ns", T2_INICIAL)
caixa_f = criar_caixa(0.76, "f MHz", F_INICIAL)
caixa_phi = criar_caixa(0.70, "phi rad", PHI_INICIAL)
caixa_C = criar_caixa(0.64, "C", C_INICIAL)
caixa_n = criar_caixa(0.58, "n (exp)", N_INICIAL)
caixa_inicio = criar_caixa(0.48, "fit início ns", FIT_START_NS)
caixa_fim = criar_caixa(0.42, "fit fim ns", FIT_STOP_NS if FIT_STOP_NS is not None else tempos[-1])

texto_resultado = fig.text(0.07, 0.27, "", fontsize=12, verticalalignment="top", family="monospace")


# ============================================================
# LÓGICA DE LEITURA E AJUSTE
# ============================================================

def _ler_parametros():
    try:
        A = float(caixa_A.text)
        T2s = float(caixa_T2.text)
        f = float(caixa_f.text)
        phi = float(caixa_phi.text)
        C = float(caixa_C.text)
        n_exp = float(caixa_n.text)
        inicio = float(caixa_inicio.text)
        fim = float(caixa_fim.text)
    except ValueError:
        texto_resultado.set_text("ERRO: digite somente números nos campos.")
        fig.canvas.draw_idle()
        return None, None, None

    if T2s <= 0 or f <= 0 or n_exp <= 0:
        texto_resultado.set_text("ERRO: T2*, f e n devem ser maiores que 0.")
        fig.canvas.draw_idle()
        return None, None, None
    if fim <= inicio:
        texto_resultado.set_text("ERRO: fim do fit precisa ser maior que o início.")
        fig.canvas.draw_idle()
        return None, None, None

    mascara = (tempos >= inicio) & (tempos <= fim)
    if np.count_nonzero(mascara) < 6:
        texto_resultado.set_text("ERRO: poucos pontos na janela escolhida para 6 parâmetros.")
        fig.canvas.draw_idle()
        return None, None, None

    params = {"A": A, "T2s": T2s, "f": f, "phi": phi, "C": C, "n": n_exp, "inicio": inicio, "fim": fim}
    return params, tempos[mascara], sinal[mascara]


def _formatar_resultado(T2s, f, n_exp, r2, n_pontos, extra=""):
    pi_ns = 1.0 / (2.0 * f / 1000.0)
    return (
        "RESULTADO{extra}\n"
        "T2* (Rabi) = {t2:.4g} ns\n"
        "f_Rabi     = {f:.4g} MHz\n"
        "n (estir.) = {n_exp:.4g}\n"
        "pi         = {pi:.4g} ns\n"
        "R^2        = {r2:.5f}\n"
        "Pontos fit = {n}\n\n"
        "Modelo:\n"
        "A exp(-(t/T2*)^n) cos(2 pi f t + phi) + C"
    ).format(extra=extra, t2=T2s, f=f, n_exp=n_exp, pi=pi_ns, r2=r2, n=n_pontos)


def atualizar(_event=None):
    params, t, y = _ler_parametros()
    if params is None:
        return

    ymodel = modelo_rabi(t, params["A"], params["T2s"], params["f"], params["phi"], params["C"], params["n"])
    r2 = calcular_r2(y, ymodel)

    t_novo = np.linspace(params["inicio"], params["fim"], 1200)
    y_novo = modelo_rabi(t_novo, params["A"], params["T2s"], params["f"], params["phi"], params["C"], params["n"])
    linha_fit.set_data(t_novo, y_novo)

    texto_resultado.set_text(_formatar_resultado(params["T2s"], params["f"], params["n"], r2, len(t), extra=" (manual)"))

    ax.relim()
    ax.autoscale_view()
    fig.canvas.draw_idle()


def ajuste_automatico(_event=None):
    params, t, y = _ler_parametros()
    if params is None:
        return

    p0 = [params["A"], params["T2s"], params["f"], params["phi"], params["C"], params["n"]]

    try:
        popt, pcov = curve_fit(
            modelo_rabi, t, y, p0=p0,
            bounds=(LIMITES_INFERIORES, LIMITES_SUPERIORES),
            maxfev=30000,
        )
    except Exception as e:
        texto_resultado.set_text("ERRO no ajuste automático:\n{}\n\nTente aproximar mais os valores manualmente\ne clique de novo.".format(e))
        fig.canvas.draw_idle()
        return

    A, T2s, f, phi, C, n_fit = popt
    perr = np.sqrt(np.diag(pcov)) if np.all(np.isfinite(pcov)) else [float("nan")] * 6

    # Atualiza as caixas com os valores refinados pelo curve_fit
    caixa_A.set_val("{:.4g}".format(A))
    caixa_T2.set_val("{:.4g}".format(T2s))
    caixa_f.set_val("{:.4g}".format(f))
    caixa_phi.set_val("{:.4g}".format(phi))
    caixa_C.set_val("{:.4g}".format(C))
    caixa_n.set_val("{:.4g}".format(n_fit))

    ymodel = modelo_rabi(t, A, T2s, f, phi, C, n_fit)
    r2 = calcular_r2(y, ymodel)
    t_novo = np.linspace(params["inicio"], params["fim"], 1200)
    linha_fit.set_data(t_novo, modelo_rabi(t_novo, A, T2s, f, phi, C, n_fit))

    extra = " (automático)\nT2* +/- {:.2g} ns | f +/- {:.2g} MHz | n +/- {:.2g}".format(perr[1], perr[2], perr[5])
    texto_resultado.set_text(_formatar_resultado(T2s, f, n_fit, r2, len(t), extra=extra))

    ax.relim()
    ax.autoscale_view()
    fig.canvas.draw_idle()


# ============================================================
# BOTÕES E BINDINGS
# ============================================================

eixo_atualizar = fig.add_axes([0.74, 0.28, 0.20, 0.05])
botao_atualizar = Button(eixo_atualizar, "Atualizar")
botao_atualizar.on_clicked(atualizar)

eixo_auto = fig.add_axes([0.74, 0.20, 0.20, 0.05])
botao_auto = Button(eixo_auto, "Ajuste automático")
botao_auto.on_clicked(ajuste_automatico)

eixo_salvar = fig.add_axes([0.74, 0.12, 0.20, 0.05])
botao_salvar = Button(eixo_salvar, "Salvar figura")


def salvar(_event=None):
    atualizar()
    nome = "T2star_Rabi_fit.png"
    fig.savefig(BASE_DIR / nome, dpi=200, bbox_inches="tight")
    print("Figura salva em:", BASE_DIR / nome)
    texto_resultado.set_text(texto_resultado.get_text() + "\n\nSalvo como:\n{}".format(nome))
    fig.canvas.draw_idle()


botao_salvar.on_clicked(salvar)

# Executa primeira renderização
atualizar()

plt.show()
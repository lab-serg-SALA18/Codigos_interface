# -*- coding: utf-8 -*-
"""Análise manual e automática de Rabi a partir de pontos_experimentais.csv.

Altere o  BASE_DIR no início do código para o caminho da pasta que contém pontos_experimentais.csv.
Modelo: y(t) = A exp(-((t/T2*)**n)) cos(2*pi*f*t + phi) + C.
Unidades: t e T2* em ns; frequência f em MHz; fase em rad.
"""
from pathlib import Path
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import TextBox, Button
from scipy.optimize import curve_fit

# =========================== CONFIGURAÇÃO ===========================
BASE_DIR = Path(r"C:\Users\lab\Desktop\NV\Joao_pedro\Rabi\Dados\18_10_2026\18_10_2026--17_58")
ARQUIVO_CSV = BASE_DIR / "pontos_experimentais.csv"
FIT_START_NS = 0.0
FIT_STOP_NS = None                 # None = último ponto
PREFERIR_SINAL_NORMALIZADO = True  # coluna sinal_normalizado_0_1 do CSV

A_INICIAL = 0.5
T2_INICIAL = 250.0
F_INICIAL_MHZ = 3.2
PHI_INICIAL = 0.0
C_INICIAL = 0.5
N_INICIAL = 1.0


def numero(celula):
    """Converte uma célula CSV para float; célula vazia resulta em NaN."""
    if celula is None or str(celula).strip() == "":
        return float("nan")
    valor = str(celula).strip()
    if "," in valor and "." not in valor:
        valor = valor.replace(",", ".")
    return float(valor)


def normalizar_minmax(valores):
    valores = np.asarray(valores, dtype=float)
    validos = np.isfinite(valores)
    saida = np.full(valores.shape, np.nan, dtype=float)
    if not np.any(validos):
        return saida
    minimo = np.min(valores[validos])
    amplitude = np.max(valores[validos]) - minimo
    if not np.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("O sinal não apresenta variação suficiente para normalizar.")
    saida[validos] = (valores[validos] - minimo) / amplitude
    return saida


def carregar_csv(caminho):
    """Lê o CSV do conversor; aceita separador vírgula ou ponto e vírgula."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError(
            "CSV não encontrado:\n{}\n\nColoque pontos_experimentais.csv na mesma pasta "
            "deste programa ou altere ARQUIVO_CSV no início do código.".format(caminho)
        )

    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(8192)
        arquivo.seek(0)
        primeira = amostra.splitlines()[0] if amostra.splitlines() else ""
        separador = ";" if primeira.count(";") > primeira.count(",") else ","
        leitor = csv.DictReader(arquivo, delimiter=separador)
        if not leitor.fieldnames:
            raise ValueError("O CSV está vazio ou não tem cabeçalho.")
        leitor.fieldnames = [x.strip() if x else x for x in leitor.fieldnames]
        colunas = set(leitor.fieldnames)
        linhas = list(leitor)

    if not linhas:
        raise ValueError("O CSV não contém pontos.")
    if "tempo_ns" not in colunas:
        raise ValueError("Não encontrei a coluna 'tempo_ns'. Colunas: {}".format(sorted(colunas)))

    tempos = np.array([numero(l.get("tempo_ns")) for l in linhas], dtype=float)
    sinal = None
    origem_sinal = None

    if PREFERIR_SINAL_NORMALIZADO and "sinal_normalizado_0_1" in colunas:
        cand = np.array([numero(l.get("sinal_normalizado_0_1")) for l in linhas], dtype=float)
        if np.any(np.isfinite(cand)):
            sinal, origem_sinal = cand, "sinal_normalizado_0_1"

    if sinal is None and "razao_sinal" in colunas:
        razao = np.array([numero(l.get("razao_sinal")) for l in linhas], dtype=float)
        sinal = normalizar_minmax(razao)
        origem_sinal = "razao_sinal normalizada pelo programa"

    if sinal is None and {"soma_pixels_MW_on", "soma_pixels_MW_off"}.issubset(colunas):
        on = np.array([numero(l.get("soma_pixels_MW_on")) for l in linhas], dtype=float)
        off = np.array([numero(l.get("soma_pixels_MW_off")) for l in linhas], dtype=float)
        if "soma_pixels_background" in colunas:
            back = np.array([numero(l.get("soma_pixels_background")) for l in linhas], dtype=float)
            use_back = np.any(np.isfinite(back))
        else:
            back, use_back = np.zeros_like(on), False
        numerador = on - back if use_back else on
        denominador = off - back if use_back else off
        razao = np.full_like(on, np.nan)
        validos = np.isfinite(numerador) & np.isfinite(denominador) & (np.abs(denominador) > 1e-15)
        razao[validos] = numerador[validos] / denominador[validos]
        sinal = normalizar_minmax(razao)
        origem_sinal = "somas de pixels convertidas em razão normalizada"

    if sinal is None:
        raise ValueError(
            "O CSV precisa ter 'sinal_normalizado_0_1', 'razao_sinal' ou as colunas "
            "'soma_pixels_MW_on' e 'soma_pixels_MW_off'. Colunas: {}".format(sorted(colunas))
        )

    ok = np.isfinite(tempos) & np.isfinite(sinal)
    tempos, sinal = tempos[ok], sinal[ok]
    ordem = np.argsort(tempos)
    tempos, sinal = tempos[ordem], sinal[ordem]
    if len(tempos) < 8:
        raise ValueError("Restaram apenas {} pontos válidos; são necessários pelo menos 8.".format(len(tempos)))
    if np.any(np.diff(tempos) <= 0):
        raise ValueError("A coluna tempo_ns deve conter valores únicos e crescentes.")
    if np.ptp(sinal) <= 0:
        raise ValueError("O sinal não varia; não é possível fazer o ajuste.")

    print("CSV lido:", caminho.resolve())
    print("Coluna de sinal usada:", origem_sinal)
    print("Pontos válidos:", len(tempos))
    print("Intervalo: {:.6g} a {:.6g} ns; passo médio: {:.6g} ns".format(
        tempos[0], tempos[-1], np.mean(np.diff(tempos))))
    return tempos, sinal


def modelo_rabi(t, A, T2s, f_mhz, phi, C, n_exp):
    """t e T2* em ns; f em MHz; n adimensional."""
    t = np.asarray(t, dtype=float)
    t_rel = t - TEMPO_ORIGEM_NS
    return (A * np.exp(-((np.maximum(t_rel, 0.0) / T2s) ** n_exp))
            * np.cos(2.0 * np.pi * (f_mhz / 1000.0) * t_rel + phi) + C)

def modelo_exponencial(t, A, T2s, C, n_exp):
    t = np.asarray(t, dtype=float)
    t_rel = t - TEMPO_ORIGEM_NS
    return (A * np.exp(-((np.maximum(t_rel, 0.0) / T2s) ** n_exp))+ C)


def calcular_r2(y, yfit):
    residuo = np.sum((y - yfit) ** 2)
    total = np.sum((y - np.mean(y)) ** 2)
    return float("nan") if total <= 0 else 1.0 - residuo / total


# ============================== DADOS ==============================
tempos, sinal = carregar_csv(ARQUIVO_CSV)
TEMPO_ORIGEM_NS = float(tempos[0])
passo_medio = float(np.mean(np.diff(tempos)))
F_NYQUIST_MHZ = 500.0 / passo_medio
F_MAX_MHZ = min(200.0, 0.95 * F_NYQUIST_MHZ)
if F_MAX_MHZ <= 0.05:
    raise ValueError("Passo temporal grande demais para o intervalo de frequência do ajuste.")
if not (0.05 < F_INICIAL_MHZ < F_MAX_MHZ):
    F_INICIAL_MHZ = max(0.06, min(3.2, F_MAX_MHZ / 2.0))
FIT_FIM_INICIAL = float(tempos[-1]) if FIT_STOP_NS is None else float(FIT_STOP_NS)
FIT_INICIO_INICIAL = max(float(tempos[0]), float(FIT_START_NS))

# ================================ GRÁFICO ================================
fig = plt.figure(figsize=(15, 9))
ax = fig.add_axes([0.07, 0.33, 0.60, 0.62])
linha_dados, = ax.plot(tempos, sinal, "ko", markersize=4, label="Dados do CSV")
tfit = np.linspace(FIT_INICIO_INICIAL, FIT_FIM_INICIAL, 1200)
linha_fit, = ax.plot(tfit, modelo_rabi(tfit, A_INICIAL, T2_INICIAL, F_INICIAL_MHZ,
                        PHI_INICIAL, C_INICIAL, N_INICIAL),"r-", linewidth=2, label="Fit_senoide")
linha_expo, = ax.plot(tfit, modelo_exponencial(tfit, A_INICIAL, T2_INICIAL, C_INICIAL, N_INICIAL),"-", linewidth=2, label="Fit_exponencial")
ax.set_xlabel("Tempo (ns)", fontsize=14)
ax.set_ylabel("Sinal normalizado", fontsize=14)
ax.grid(alpha=0.3)
ax.legend(fontsize=12)


def criar_caixa(y, nome, valor):
    return TextBox(fig.add_axes([0.74, y, 0.20, 0.04]), nome, initial=str(valor))

caixa_A = criar_caixa(0.88, "A", A_INICIAL)
caixa_T2 = criar_caixa(0.82, "T2* ns", T2_INICIAL)
caixa_f = criar_caixa(0.76, "f MHz", F_INICIAL_MHZ)
caixa_phi = criar_caixa(0.70, "phi rad", PHI_INICIAL)
caixa_C = criar_caixa(0.64, "C", C_INICIAL)
caixa_n = criar_caixa(0.58, "n (exp)", N_INICIAL)
caixa_inicio = criar_caixa(0.48, "fit início ns", FIT_INICIO_INICIAL)
caixa_fim = criar_caixa(0.42, "fit fim ns", FIT_FIM_INICIAL)
texto_resultado = fig.text(0.07, 0.27, "", fontsize=10.5, verticalalignment="top", family="monospace")


def ler_parametros():
    try:
        p = {
            "A": float(caixa_A.text), "T2s": float(caixa_T2.text),
            "f": float(caixa_f.text), "phi": float(caixa_phi.text),
            "C": float(caixa_C.text), "n": float(caixa_n.text),
            "inicio": float(caixa_inicio.text), "fim": float(caixa_fim.text),
        }
    except ValueError:
        texto_resultado.set_text("ERRO: digite somente números nos campos.")
        fig.canvas.draw_idle()
        return None, None, None
    if not all(np.isfinite(v) for v in p.values()):
        texto_resultado.set_text("ERRO: todos os parâmetros devem ser finitos.")
        fig.canvas.draw_idle()
        return None, None, None
    if p["T2s"] <= 0 or p["f"] <= 0 or p["n"] <= 0:
        texto_resultado.set_text("ERRO: T2*, frequência e n precisam ser positivos.")
        fig.canvas.draw_idle()
        return None, None, None
    if p["fim"] <= p["inicio"] or p["inicio"] < tempos[0] or p["fim"] > tempos[-1]:
        texto_resultado.set_text("ERRO: janela de ajuste inválida; use o intervalo {:.6g}–{:.6g} ns.".format(tempos[0], tempos[-1]))
        fig.canvas.draw_idle()
        return None, None, None
    mask = (tempos >= p["inicio"]) & (tempos <= p["fim"])
    if np.count_nonzero(mask) < 8:
        texto_resultado.set_text("ERRO: selecione pelo menos 8 pontos na janela do ajuste.")
        fig.canvas.draw_idle()
        return None, None, None
    return p, tempos[mask], sinal[mask]


def formatar_resultado(T2s, f, n_exp, r2, n_pontos, extra=""):
    t_pi = 1.0 / (2.0 * f / 1000.0)
    aviso = "\nAVISO: R² baixo; confira a janela e o modelo." if np.isfinite(r2) and r2 < 0.80 else ""
    return ("RESULTADO{}\nT2* (Rabi) = {:.6g} ns\nf_Rabi = {:.6g} MHz\n"
            "n (estir.) = {:.5g}\npulso pi estimado = {:.6g} ns\nR² = {:.6f}\n"
            "Pontos fit = {}\nNyquist estimado = {:.6g} MHz{}\n\n"
            "Modelo: A exp(-((t/T2*)^n)) cos(2π f t + φ) + C").format(
                extra, T2s, f, n_exp, t_pi, r2, n_pontos, F_NYQUIST_MHZ, aviso)


def atualizar(_event=None):
    p, t, y = ler_parametros()
    if p is None:
        return
    fit = modelo_rabi(t, p["A"], p["T2s"], p["f"], p["phi"], p["C"], p["n"])
    r2 = calcular_r2(y, fit)
    t_novo = np.linspace(p["inicio"], p["fim"], 1200)
    linha_fit.set_data(t_novo, modelo_rabi(t_novo, p["A"], p["T2s"], p["f"], p["phi"], p["C"], p["n"]))
    linha_expo.set_data(t_novo, modelo_exponencial(t_novo, p["A"], p["T2s"], p["C"], p["n"]))
    texto_resultado.set_text(formatar_resultado(p["T2s"], p["f"], p["n"], r2, len(t), " (manual)"))
    ax.relim(); ax.autoscale_view(); fig.canvas.draw_idle()
    print("Atualizado: A = {:.6g}; T2* = {:.6g} ns; f = {:.6g} MHz; phi = {:.6g}; n = {:.5g}; C = {:.6f}".format(p["A"], p["T2s"], p["f"], p["phi"], p["n"], p["C"]))

def ajuste_automatico(_event=None):
    p, t, y = ler_parametros()
    if p is None:
        return
    lower = [0.0, 1.0, 0.05, -np.pi, -1.0, 0.1]
    upper = [3.0, 1.0e6, F_MAX_MHZ, np.pi, 2.0, 5.0]
    p0 = [p["A"], p["T2s"], p["f"], p["phi"], p["C"], p["n"]]
    p0 = [min(max(v, lo + 1e-8), hi - 1e-8) for v, lo, hi in zip(p0, lower, upper)]
    try:
        popt, pcov = curve_fit(modelo_rabi, t, y, p0=p0, bounds=(lower, upper), maxfev=100000)
    except Exception as exc:
        texto_resultado.set_text("ERRO no ajuste automático:\n{}\n\nConfira os chutes iniciais e a janela.".format(exc))
        fig.canvas.draw_idle()
        return
    A, T2s, f, phi, C, n_fit = popt
    if pcov.shape == (6, 6) and np.all(np.isfinite(pcov)) and np.all(np.diag(pcov) >= 0):
        erros = np.sqrt(np.diag(pcov))
    else:
        erros = np.full(6, np.nan)
    for caixa, valor in [(caixa_A, A), (caixa_T2, T2s), (caixa_f, f),
                         (caixa_phi, phi), (caixa_C, C), (caixa_n, n_fit)]:
        caixa.set_val("{:.6g}".format(valor))
    fit = modelo_rabi(t, A, T2s, f, phi, C, n_fit)
    r2 = calcular_r2(y, fit)
    t_novo = np.linspace(p["inicio"], p["fim"], 1200)
    linha_fit.set_data(t_novo, modelo_rabi(t_novo, A, T2s, f, phi, C, n_fit))
    linha_expo.set_data(t_novo, modelo_exponencial(t_novo, A, T2s, C, n_fit))
    extra = " (automático)\nIncertezas: T2* ± {:.3g} ns; f ± {:.3g} MHz; n ± {:.3g}".format(erros[1], erros[2], erros[5])
    avisos = []
    if np.isfinite(r2) and r2 < 0.80:
        avisos.append("R² baixo")
    if np.isfinite(erros[1]) and erros[1] > T2s:
        avisos.append("incerteza de T2* maior que T2*")
    if np.isclose(n_fit, lower[5], atol=1e-3) or np.isclose(n_fit, upper[5], atol=1e-3):
        avisos.append("n atingiu um limite")
    if np.isclose(f, upper[2], rtol=1e-3):
        avisos.append("frequência atingiu limite superior")
    if avisos:
        extra += "\nAVISO: " + "; ".join(avisos)
    texto_resultado.set_text(formatar_resultado(T2s, f, n_fit, r2, len(t), extra))
    ax.relim(); ax.autoscale_view(); fig.canvas.draw_idle()
    print(extra)


botao_atualizar = Button(fig.add_axes([0.74, 0.28, 0.20, 0.05]), "Atualizar")
botao_atualizar.on_clicked(atualizar)
botao_auto = Button(fig.add_axes([0.74, 0.20, 0.20, 0.05]), "Ajuste automático")
botao_auto.on_clicked(ajuste_automatico)
botao_salvar = Button(fig.add_axes([0.74, 0.12, 0.20, 0.05]), "Salvar figura")


def salvar(_event=None):
    nome = "T2star_Rabi_fit.png"
    saida = BASE_DIR / nome
    fig.savefig(saida, dpi=200, bbox_inches="tight")
    print("Figura salva em:", saida.resolve())


botao_salvar.on_clicked(salvar)
atualizar()
plt.show()

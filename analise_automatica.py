# -*- coding: utf-8 -*-
"""
Funções de análise automática usadas pela interface:

 - detectar_ressonancias / melhor_ressonancia:
   encontram os vales do espectro de ODMR e apontam o de maior contraste.

 - ajuste_automatico:
   recebe o código da sequência (RABI, T1, HAN, RAM, CPMGx, ESEEM) e devolve
   o T1 / T2 / T2* / frequência de Rabi / frequência de modulação (ESEEM)
   correspondente, já com uma curva de ajuste pronta para plotar. O Rabi
   também devolve um T2* (do decaimento da própria oscilação, ver o
   docstring de ajustar_rabi) quando o ajuste amortecido converge.

Todas as funções são "seguras": se o ajuste falhar (poucos pontos, sinal
ruidoso demais, etc.) elas devolvem {"sucesso": False, "mensagem": ...} em
vez de lançar uma exceção que derrubaria a medida no meio do caminho.

Este módulo não depende de nenhum hardware — só numpy/scipy — então dá pra
testar sozinho com dados simulados.
"""

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, savgol_filter


# ===================================================================== #
# ODMR — detecção de ressonâncias
# ===================================================================== #
def calcular_r2(y, y_fit):
    y = np.asarray(y, dtype=float)
    y_fit = np.asarray(y_fit, dtype=float)

    ss_res = np.sum((y - y_fit) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)

    if ss_tot == 0:
        return float("nan")

    return 1.0 - ss_res / ss_tot


def detectar_ressonancias(freqs, pl, prominence=0.015):
    """
    Encontra os vales (ressonâncias) no espectro de ODMR.

    `pl` deve ser o sinal normalizado pelo máximo (como o odmr.py já fazia:
    mean_signal /= max(mean_signal)), então o nível de fundo fica perto de
    1.0 e cada ressonância aparece como um vale abaixo disso. O contraste
    de cada vale é definido como 1.0 - valor_no_vale.

    Retorna uma lista de dicts ordenada da maior para a menor contraste:
        [{"freq": ..., "valor": ..., "contraste": ...}, ...]
    """
    freqs = np.asarray(freqs, dtype=float)
    pl = np.asarray(pl, dtype=float)
    n = len(pl)
    if n < 5:
        return []

    janela = min(9, n if n % 2 == 1 else n - 1)
    janela = max(janela, 3)
    polyorder = min(2, janela - 1)
    suave = savgol_filter(pl, window_length=janela, polyorder=polyorder)

    indices, _ = find_peaks(-suave, prominence=prominence)

    resultado = [
        {"freq": float(freqs[i]), "valor": float(pl[i]), "contraste": float(1.0 - pl[i])}
        for i in indices
    ]
    resultado.sort(key=lambda d: d["contraste"], reverse=True)
    return resultado


def melhor_ressonancia(freqs, pl, prominence=0.015):
    """Só a ressonância de maior contraste (ou None se nada for encontrado)."""
    todas = detectar_ressonancias(freqs, pl, prominence=prominence)
    return todas[0] if todas else None


# ===================================================================== #
# MODELOS DE AJUSTE
# ===================================================================== #

def _exp_decay(t, A, T, C):
    return A * np.exp(-t / T) + C


def _exp_stretched(t, A, T, C, p):
    return A * np.exp(-((t / T) ** p)) + C


def _cosseno_amortecido(t, A, f, phi, T, C, n=1.0):
    """Cosseno amortecido por exponencial estirada: A * exp(-(t/T)^n) * cos(2*pi*f*t + phi) + C"""
    return A * np.exp(-((t / T) ** n)) * np.cos(2 * np.pi * f * t + phi) + C


def _cosseno_puro(t, A, f, phi, C):
    return A * np.cos(2 * np.pi * f * t + phi) + C


def _ramsey(t, A, f, phi, T, C):
    return A * np.exp(-t / T) * np.cos(2 * np.pi * f * t + phi) + C


def _estimar_frequencia_fft(t, y):
    """Estimativa inicial de frequência de oscilação via FFT (ignora o pico DC)."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float) - np.mean(y)
    if len(t) < 4:
        return 0.0
    dt = np.mean(np.diff(t))
    if dt <= 0:
        return 0.0
    espectro = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), d=dt)
    if len(espectro) < 2:
        return 0.0
    idx = int(np.argmax(espectro[1:])) + 1  # ignora a componente DC
    return float(freqs[idx])


# ===================================================================== #
# AJUSTES ESPECÍFICOS
# ===================================================================== #

def ajustar_exponencial(t, y, nome="T", estica=True, unidade="ns"):
    """T1 / T2 (Hahn, CPMG) — decaimento exponencial simples ou esticado."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 4:
        return {"sucesso": False, "mensagem": "Poucos pontos para ajustar."}

    span = (t[-1] - t[0]) or 1.0
    T0 = span / 2.0
    A0 = (y[0] - y[-1]) or (y.max() - y.min()) or 1.0
    C0 = float(y[-1])

    try:
        if estica:
            popt, pcov = curve_fit(
                _exp_stretched, t, y,
                p0=[A0, T0, C0, 1.0],
                bounds=([-np.inf, 1e-6, -np.inf, 0.3], [np.inf, np.inf, np.inf, 4.0]),
                maxfev=20000,
            )
            A, T, C, p = popt
            var_T = pcov[1, 1]
            curva = lambda tt, p=popt: _exp_stretched(tt, *p)
            extra = {"expoente_estica": float(p)}
        else:
            popt, pcov = curve_fit(
                _exp_decay, t, y,
                p0=[A0, T0, C0],
                bounds=([-np.inf, 1e-6, -np.inf], [np.inf, np.inf, np.inf]),
                maxfev=20000,
            )
            A, T, C = popt
            var_T = pcov[1, 1]
            curva = lambda tt, p=popt: _exp_decay(tt, *p)
            extra = {}
    except Exception as e:
        return {"sucesso": False, "mensagem": f"Ajuste falhou: {e}"}
    
    if nome == "T1":
        erro_T = float(np.sqrt(var_T)/1000) if np.isfinite(var_T) and var_T > 0 else float("nan")
    else:
        erro_T = float(np.sqrt(var_T)) if np.isfinite(var_T) and var_T > 0 else float("nan")
    t_fino = np.linspace(t.min(), t.max(), 300)
    r2 = calcular_r2(y, curva(t))
    resultado = {
        "sucesso": True,
        "rotulo": nome,
        "valor": float(T/1000),
        "unidade": unidade,
        "incerteza": erro_T,
        "r2": r2,
        "curva_fit": (t_fino, curva(t_fino)),
    }
    resultado.update(extra)
    return resultado


def ajustar_rabi(t, y):
    """Frequência de Rabi — ajuste de cosseno amortecido por exponencial estirada (quando converge)."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 6:
        return {"sucesso": False, "mensagem": "Poucos pontos para ajustar."}

    f0 = _estimar_frequencia_fft(t, y)
    if f0 <= 0:
        f0 = 1.0 / max(2 * (t[-1] - t[0]), 1e-9)
    A0 = (y.max() - y.min()) / 2.0 or 1.0
    C0 = float(y.mean())
    T0 = (t[-1] - t[0]) * 2.0 or 1.0
    n0 = 1.0

    f = erro_f = curva = None
    t2_star_ns = erro_t2_star_ns = None
    n_fit = erro_n = None

    try:
        # p0: [A, f, phi, T, C, n]
        popt, pcov = curve_fit(
            _cosseno_amortecido, t, y, p0=[A0, f0, 0.0, T0, C0, n0],
            bounds=([-np.inf, -np.inf, -np.inf, 1e-6, -np.inf, 0.1], [np.inf, np.inf, np.inf, np.inf, np.inf, 5.0]),
            maxfev=30000,
        )
        f = abs(popt[1])
        erro_f = float(np.sqrt(pcov[1, 1])) if np.isfinite(pcov[1, 1]) and pcov[1, 1] > 0 else float("nan")
        curva = lambda tt, p=popt: _cosseno_amortecido(tt, *p)

        t2_star_ns = float(popt[3])
        var_t2 = pcov[3, 3]
        erro_t2_star_ns = float(np.sqrt(var_t2)) if np.isfinite(var_t2) and var_t2 > 0 else float("nan")

        n_fit = float(popt[5])
        var_n = pcov[5, 5]
        erro_n = float(np.sqrt(var_n)) if np.isfinite(var_n) and var_n > 0 else float("nan")
    except Exception:
        try:
            popt, pcov = curve_fit(_cosseno_puro, t, y, p0=[A0, f0, 0.0, C0], maxfev=30000)
            f = abs(popt[1])
            erro_f = float(np.sqrt(pcov[1, 1])) if np.isfinite(pcov[1, 1]) and pcov[1, 1] > 0 else float("nan")
            curva = lambda tt, p=popt: _cosseno_puro(tt, *p)
        except Exception as e:
            return {"sucesso": False, "mensagem": f"Ajuste de Rabi falhou: {e}"}

    if f is None or not np.isfinite(f) or f <= 0:
        return {"sucesso": False, "mensagem": "Não foi possível identificar uma oscilação clara."}

    valor_mhz = f * 1000.0
    erro_mhz = erro_f * 1000.0 if np.isfinite(erro_f) else erro_f
    pulso_pi_ns = 1.0 / (2 * f)

    t_fino = np.linspace(t.min(), t.max(), 300)
    r2 = calcular_r2(y, curva(t))
    resultado = {
        "sucesso": True,
        "rotulo": "Frequência de Rabi",
        "valor": float(valor_mhz),
        "unidade": "MHz",
        "incerteza": erro_mhz,
        "curva_fit": (t_fino, curva(t_fino)),
        "pulso_pi_estimado_ns": float(pulso_pi_ns),
        "r2": r2,
    }

    if t2_star_ns is not None and np.isfinite(t2_star_ns) and t2_star_ns > 0:
        resultado["t2_star_rabi_rotulo"] = "T2* (Rabi)"
        resultado["t2_star_rabi_ns"] = t2_star_ns
        resultado["t2_star_rabi_incerteza_ns"] = erro_t2_star_ns
        resultado["expoente_n"] = n_fit
        resultado["expoente_n_incerteza"] = erro_n

    return resultado


def ajustar_ramsey(t, y, unidade="ns"):
    """T2* — tenta franja amortecida; se não convergir bem, cai para exponencial simples."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 5:
        return {"sucesso": False, "mensagem": "Poucos pontos para ajustar."}

    f0 = _estimar_frequencia_fft(t, y)
    A0 = (y.max() - y.min()) / 2.0 or 1.0
    C0 = float(y.mean())
    T0 = ((t[-1] - t[0]) / 2.0) or 1.0

    try:
        popt, pcov = curve_fit(
            _ramsey, t, y, p0=[A0, f0, 0.0, T0, C0],
            bounds=([-np.inf, -np.inf, -np.inf, 1e-6, -np.inf], [np.inf, np.inf, np.inf, np.inf, np.inf]),
            maxfev=30000,
        )
        T = popt[3]
        var_T = pcov[3, 3]
        erro_T = float(np.sqrt(var_T)) if np.isfinite(var_T) and var_T > 0 else float("nan")
        curva = lambda tt, p=popt: _ramsey(tt, *p)
        t_fino = np.linspace(t.min(), t.max(), 300)
        r2 = calcular_r2(y, curva(t))
        return {
            "sucesso": True,
            "rotulo": "T2*",
            "valor": float(T),
            "unidade": unidade,
            "incerteza": erro_T,
            "r2": r2,
            "curva_fit": (t_fino, curva(t_fino)),
            "frequencia_franja": float(abs(popt[1])),
        }
    except Exception:
        return ajustar_exponencial(t, y, nome="T2*", estica=False, unidade=unidade)


def analisar_eseem(t, y):
    """
    ESEEM não tem um 'T' — a grandeza física relevante é a frequência de
    modulação do envelope do eco (acoplamento hiperfino com núcleos vizinhos).
    Remove a tendência lenta (envelope de decaimento) e faz FFT do resíduo.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 8:
        return {"sucesso": False, "mensagem": "Poucos pontos para FFT."}

    tendencia = np.polyval(np.polyfit(t, y, deg=2), t)
    residuo = (y - tendencia) * np.hanning(len(y))

    dt = np.mean(np.diff(t))
    if dt <= 0:
        return {"sucesso": False, "mensagem": "Pontos de tempo inválidos."}

    espectro = np.abs(np.fft.rfft(residuo))
    freqs = np.fft.rfftfreq(len(t), d=dt)
    if len(espectro) < 2:
        return {"sucesso": False, "mensagem": "Sinal insuficiente para FFT."}

    idx = int(np.argmax(espectro[1:])) + 1
    freq_dominante_mhz = float(freqs[idx]) * 1000.0  # ciclos/ns -> MHz

    return {
        "sucesso": True,
        "rotulo": "Frequência de modulação (ESEEM)",
        "valor": freq_dominante_mhz,
        "unidade": "MHz",
        "incerteza": float("nan"),
        "espectro_fft": (freqs * 1000.0, espectro),  # também em MHz
        "curva_fit": None,
    }


# ===================================================================== #
# DESPACHANTE — escolhe o ajuste certo para cada tipo de sequência
# ===================================================================== #

_UNIDADE_POR_SEQUENCIA = {
    "RABI": "ns", "T1": "ms", "HAN": "us", "RAM": "ns",
    "CPMG2": "ns", "CPMG4": "ns", "CPMG8": "ns", "ESEEM": "ns",
}


def ajuste_automatico(sequencia, tempos, sinal):
    """
    Recebe o código da sequência (RABI, T1, HAN, RAM, CPMG2, CPMG4, CPMG8,
    ESEEM) e devolve o ajuste apropriado.
    """
    seq = sequencia.upper()
    unidade = _UNIDADE_POR_SEQUENCIA.get(seq, "ns")
    tempos = np.asarray(tempos, dtype=float)
    sinal = np.asarray(sinal, dtype=float)

    if seq == "RABI":
        return ajustar_rabi(tempos, sinal)
    if seq == "T1":
        return ajustar_exponencial(tempos, sinal, nome="T1", estica=False, unidade=unidade) 
    if seq == "HAN":
        return ajustar_exponencial(tempos, sinal, nome="T2 (Hahn)", estica=True, unidade=unidade)
    if seq == "RAM":
        return ajustar_ramsey(tempos, sinal, unidade=unidade)
    if seq.startswith("CPMG"):
        return ajustar_exponencial(tempos, sinal, nome=f"T2 ({seq})", estica=True, unidade=unidade)
    if seq == "ESEEM":
        return analisar_eseem(tempos, sinal)

    return {"sucesso": False, "mensagem": f"Sequência '{sequencia}' não reconhecida."}
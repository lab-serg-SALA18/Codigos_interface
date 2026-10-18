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
# VALIDAÇÃO E AJUDA PARA AJUSTES
# ===================================================================== #
def _validar_dados_ajuste(t, y, min_pontos=4):
    """Valida os vetores antes de chamar o otimizador."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)

    if t.ndim != 1 or y.ndim != 1:
        return None, None, "Os tempos e o sinal precisam ser vetores 1D."
    if len(t) != len(y):
        return None, None, "O vetor de tempos e o sinal têm tamanhos diferentes."
    if len(t) < min_pontos:
        return None, None, "Poucos pontos para ajustar."
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)):
        return None, None, "Os dados contêm NaN ou infinito."
    if np.any(np.diff(t) <= 0):
        return None, None, "Os tempos precisam estar em ordem estritamente crescente e sem repetição."
    if np.ptp(t) <= 0:
        return None, None, "O intervalo temporal é nulo."
    if np.ptp(y) <= 1e-12 * max(1.0, float(np.max(np.abs(y)))):
        return None, None, "O sinal não apresenta variação suficiente para o ajuste."
    return t, y, None


def _melhor_curve_fit(modelo, t, y, iniciais, limites, maxfev=30000):
    """Testa vários chutes iniciais e devolve o ajuste com menor soma de resíduos."""
    melhor = None
    erros = []
    for p0 in iniciais:
        try:
            popt, pcov = curve_fit(
                modelo, t, y, p0=p0, bounds=limites, maxfev=maxfev
            )
            previsto = modelo(t, *popt)
            rss = float(np.sum((y - previsto) ** 2))
            if np.isfinite(rss) and (melhor is None or rss < melhor[0]):
                melhor = (rss, popt, pcov)
        except Exception as exc:
            erros.append(str(exc))
    if melhor is None:
        detalhe = erros[-1] if erros else "nenhum candidato convergiu"
        raise RuntimeError(detalhe)
    return melhor[1], melhor[2]


def _erro_parametro(pcov, indice, fator=1.0):
    try:
        variancia = float(pcov[indice, indice])
        if np.isfinite(variancia) and variancia > 0:
            return float(np.sqrt(variancia) * fator)
    except Exception:
        pass
    return float("nan")


def _avisos_qualidade(r2, erro, valor, T_variavel, span):
    """Heurísticas conservadoras: não descartam o fit, mas avisam quando desconfiar."""
    avisos = []
    if not np.isfinite(r2) or r2 < 0.80:
        avisos.append("R² baixo: a curva pode não descrever bem os dados.")
    if not np.isfinite(erro):
        avisos.append("A incerteza não pôde ser estimada de forma confiável.")
    elif np.isfinite(valor) and abs(valor) > 0 and erro / abs(valor) > 0.5:
        avisos.append("A incerteza é maior que 50% do valor ajustado.")
    if np.isfinite(T_variavel) and T_variavel > 5.0 * span:
        avisos.append("A janela temporal é curta comparada ao tempo de decaimento ajustado.")
    return avisos


# ===================================================================== #
# AJUSTES ESPECÍFICOS
# ===================================================================== #

def ajustar_exponencial(
    t, y, nome="T", estica=True, unidade="ns",
    fator_tempo_total=1.0, fator_conversao=1000.0,
):
    """
    Ajusta decaimento simples ou esticado.

    fator_tempo_total converte a variável varrida no tempo físico total.
    Ex.: em HAN, t é cada intervalo tau, portanto o tempo livre total é 2*t.

    fator_conversao converte a unidade do eixo para a unidade apresentada:
    1000 para ns->us ou us->ms; 1 para manter ns->ns.
    """
    t, y, erro = _validar_dados_ajuste(t, y, min_pontos=4)
    if erro:
        return {"sucesso": False, "mensagem": erro}
    if not np.isfinite(fator_tempo_total) or fator_tempo_total <= 0:
        return {"sucesso": False, "mensagem": "fator_tempo_total precisa ser positivo."}
    if not np.isfinite(fator_conversao) or fator_conversao <= 0:
        return {"sucesso": False, "mensagem": "fator_conversao precisa ser positivo."}

    span = float(t[-1] - t[0])
    passo_min = float(np.min(np.diff(t)))
    T_min = max(span * 1e-9, 1e-9)
    T_chutes = sorted(set(max(T_min * 10.0, span * r) for r in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0)))
    C0 = float(np.median(y[-max(2, len(y) // 5):]))
    A0 = float(y[0] - C0)
    if abs(A0) < 0.05 * np.ptp(y):
        A0 = float(y[0] - y[-1])
    if abs(A0) < 1e-12:
        A0 = float(np.ptp(y))

    try:
        if estica:
            p_chutes = (0.6, 1.0, 1.5, 2.0)
            iniciais = [
                [A0, T0, C0, p0]
                for T0 in T_chutes
                for p0 in p_chutes
            ]
            popt, pcov = _melhor_curve_fit(
                _exp_stretched, t, y, iniciais,
                ([-np.inf, T_min, -np.inf, 0.3],
                 [np.inf, np.inf, np.inf, 4.0]),
                maxfev=25000,
            )
            A, T, C, p = popt
            modelo = lambda tt, pars=popt: _exp_stretched(tt, *pars)
            extra = {"expoente_estica": float(p)}
            n_parametros = 4
        else:
            iniciais = [[A0, T0, C0] for T0 in T_chutes]
            popt, pcov = _melhor_curve_fit(
                _exp_decay, t, y, iniciais,
                ([-np.inf, T_min, -np.inf], [np.inf, np.inf, np.inf]),
                maxfev=25000,
            )
            A, T, C = popt
            modelo = lambda tt, pars=popt: _exp_decay(tt, *pars)
            extra = {}
            n_parametros = 3
    except Exception as exc:
        return {"sucesso": False, "mensagem": "Ajuste exponencial falhou: {}".format(exc)}

    T_fisico = float(T * fator_tempo_total)
    erro_T = _erro_parametro(pcov, 1, fator_tempo_total / fator_conversao)
    valor = T_fisico / fator_conversao
    t_fino = np.linspace(t.min(), t.max(), 400)
    y_fit = modelo(t)
    r2 = calcular_r2(y, y_fit)
    avisos = _avisos_qualidade(r2, erro_T, valor, T, span)

    resultado = {
        "sucesso": True,
        "rotulo": nome,
        "valor": float(valor),
        "unidade": unidade,
        "incerteza": erro_T,
        "r2": float(r2),
        "curva_fit": (t_fino, modelo(t_fino)),
        "confiavel": len(avisos) == 0,
        "avisos": avisos,
        "escala_ajuste": float(T),
        "fator_tempo_total": float(fator_tempo_total),
    }
    resultado.update(extra)
    return resultado

def ajustar_rabi(t, y):
    """Ajuste amortecido de Rabi com múltiplos chutes iniciais e aviso de qualidade."""
    t, y, erro = _validar_dados_ajuste(t, y, min_pontos=6)
    if erro:
        return {"sucesso": False, "mensagem": erro}

    span = float(t[-1] - t[0])
    dt = float(np.median(np.diff(t)))
    nyquist = 0.5 / dt
    f_fft = _estimar_frequencia_fft(t, y)
    f_res = 1.0 / span
    if not np.isfinite(f_fft) or f_fft <= 0:
        f_fft = f_res
    f_min = max(1e-12, f_res * 0.02)
    f_max = nyquist * 0.999
    f_chutes = sorted(set(
        min(max(float(f), f_min * 2.0), f_max * 0.95)
        for f in (f_fft, 0.8 * f_fft, 1.2 * f_fft, f_res)
    ))

    A0 = float(np.ptp(y) / 2.0)
    C0 = float(np.mean(y))
    T0s = (span, 2.0 * span, 4.0 * span)
    T_min = max(span * 1e-9, 1e-9)
    iniciais = [
        [A0, f0, fase, T0, C0, 1.0]
        for f0 in f_chutes
        for fase in (0.0, np.pi / 2.0)
        for T0 in T0s
    ]
    limites = (
        [-np.inf, f_min, -4.0 * np.pi, T_min, -np.inf, 0.1],
        [np.inf, f_max, 4.0 * np.pi, np.inf, np.inf, 5.0],
    )

    try:
        popt, pcov = _melhor_curve_fit(
            _cosseno_amortecido, t, y, iniciais, limites, maxfev=30000
        )
        A, f_fit, phi, T, C, n_fit = popt
        modelo = lambda tt, pars=popt: _cosseno_amortecido(tt, *pars)
        erro_f = _erro_parametro(pcov, 1, 1000.0)
        erro_T = _erro_parametro(pcov, 3)
        erro_n = _erro_parametro(pcov, 5)
        t2_star_ns = float(T)
    except Exception as exc:
        # Fallback transparente: o cosseno puro não permite medir T2* do Rabi.
        try:
            iniciais_puros = [
                [A0, f0, fase, C0]
                for f0 in f_chutes
                for fase in (0.0, np.pi / 2.0)
            ]
            popt, pcov = _melhor_curve_fit(
                _cosseno_puro, t, y, iniciais_puros,
                ([-np.inf, f_min, -4.0 * np.pi, -np.inf],
                 [np.inf, f_max, 4.0 * np.pi, np.inf]),
                maxfev=30000,
            )
            A, f_fit, phi, C = popt
            modelo = lambda tt, pars=popt: _cosseno_puro(tt, *pars)
            erro_f = _erro_parametro(pcov, 1, 1000.0)
            erro_T = float("nan")
            erro_n = float("nan")
            t2_star_ns = None
            n_fit = None
        except Exception as exc_puro:
            return {"sucesso": False, "mensagem": "Ajuste de Rabi falhou: {} (fallback: {})".format(exc, exc_puro)}

    f_fit = abs(float(f_fit))
    if not np.isfinite(f_fit) or f_fit <= 0:
        return {"sucesso": False, "mensagem": "Não foi possível identificar uma oscilação clara."}

    valor_mhz = f_fit * 1000.0
    pulso_pi_ns = 1.0 / (2.0 * f_fit)
    t_fino = np.linspace(t.min(), t.max(), 400)
    r2 = calcular_r2(y, modelo(t))
    avisos = _avisos_qualidade(r2, erro_f, valor_mhz, T if t2_star_ns is not None else span, span)
    if t2_star_ns is None:
        avisos.append("Foi usado cosseno puro; T2* do envelope não foi determinado.")

    resultado = {
        "sucesso": True,
        "rotulo": "Frequência de Rabi",
        "valor": float(valor_mhz),
        "unidade": "MHz",
        "incerteza": erro_f,
        "curva_fit": (t_fino, modelo(t_fino)),
        "pulso_pi_estimado_ns": float(pulso_pi_ns),
        "r2": float(r2),
        "confiavel": len(avisos) == 0,
        "avisos": avisos,
    }
    if t2_star_ns is not None and np.isfinite(t2_star_ns) and t2_star_ns > 0:
        resultado.update({
            "t2_star_rabi_rotulo": "T2* (Rabi)",
            "t2_star_rabi_ns": float(t2_star_ns),
            "t2_star_rabi_incerteza_ns": erro_T,
            "expoente_n": float(n_fit),
            "expoente_n_incerteza": erro_n,
        })
    return resultado

def ajustar_ramsey(t, y, unidade="ns"):
    """Ajuste das franjas de Ramsey com chutes múltiplos e validação do resultado."""
    t, y, erro = _validar_dados_ajuste(t, y, min_pontos=5)
    if erro:
        return {"sucesso": False, "mensagem": erro}

    span = float(t[-1] - t[0])
    dt = float(np.median(np.diff(t)))
    nyquist = 0.5 / dt
    f_fft = _estimar_frequencia_fft(t, y)
    f_res = 1.0 / span
    if not np.isfinite(f_fft) or f_fft <= 0:
        f_fft = f_res
    f_min = max(1e-12, f_res * 0.02)
    f_max = nyquist * 0.999
    f_chutes = sorted(set(
        min(max(float(f), f_min * 2.0), f_max * 0.95)
        for f in (f_fft, 0.7 * f_fft, 1.3 * f_fft, f_res)
    ))
    T_min = max(span * 1e-9, 1e-9)
    T_chutes = (max(span * 0.25, T_min * 10.0), span, 2.0 * span)
    A0 = float(np.ptp(y) / 2.0)
    C0 = float(np.mean(y))
    iniciais = [
        [A0, f0, fase, T0, C0]
        for f0 in f_chutes
        for fase in (0.0, np.pi / 2.0, -np.pi / 2.0)
        for T0 in T_chutes
    ]

    try:
        popt, pcov = _melhor_curve_fit(
            _ramsey, t, y, iniciais,
            ([-np.inf, f_min, -4.0 * np.pi, T_min, -np.inf],
             [np.inf, f_max, 4.0 * np.pi, np.inf, np.inf]),
            maxfev=30000,
        )
        A, f_fit, phi, T, C = popt
        modelo = lambda tt, pars=popt: _ramsey(tt, *pars)
        erro_T = _erro_parametro(pcov, 3)
        erro_f = _erro_parametro(pcov, 1)
        t_fino = np.linspace(t.min(), t.max(), 400)
        r2 = calcular_r2(y, modelo(t))
        avisos = _avisos_qualidade(r2, erro_T, T, T, span)
        if f_fit <= 1.1 * f_min:
            avisos.append("A frequência de franja ficou próxima do limite inferior; verifique a dessintonia e o eixo temporal.")
        if f_fit >= 0.95 * f_max:
            avisos.append("A frequência de franja ficou próxima do limite de Nyquist; o passo temporal pode ser grande demais.")

        return {
            "sucesso": True,
            "rotulo": "T2*",
            "valor": float(T),
            "unidade": unidade,
            "incerteza": erro_T,
            "r2": float(r2),
            "curva_fit": (t_fino, modelo(t_fino)),
            "frequencia_franja": float(abs(f_fit)),
            "frequencia_franja_incerteza": erro_f,
            "confiavel": len(avisos) == 0,
            "avisos": avisos,
            "modelo_usado": "Ramsey amortecido",
        }
    except Exception as exc_ramsey:
        # Sem franjas identificáveis, ainda tentamos obter apenas um envelope.
        exp = ajustar_exponencial(
            t, y, nome="T2*", estica=False, unidade=unidade,
            fator_tempo_total=1.0, fator_conversao=1.0,
        )
        if exp.get("sucesso"):
            exp["modelo_usado"] = "Exponencial simples (sem franja Ramsey confiável)"
            exp.setdefault("avisos", []).append(
                "O ajuste de Ramsey falhou ({}); o valor retornado é só um tempo de decaimento, não uma franja Ramsey ajustada.".format(exc_ramsey)
            )
            exp["confiavel"] = False
            return exp
        return {
            "sucesso": False,
            "mensagem": "O ajuste Ramsey falhou: {}. O ajuste exponencial também falhou: {}".format(
                exc_ramsey, exp.get("mensagem", "erro desconhecido")
            ),
        }

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
    "CPMG2": "us", "CPMG4": "us", "CPMG8": "us", "ESEEM": "ns",
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
    if tempos.ndim != 1 or sinal.ndim != 1 or len(tempos) != len(sinal):
        return {"sucesso": False, "mensagem": "Tempos e sinal precisam ser vetores 1D do mesmo tamanho."}
    if len(tempos) > 1 and np.any(np.diff(tempos) <= 0):
        return {"sucesso": False, "mensagem": "Os tempos precisam estar em ordem crescente e sem repetição."}

    if seq == "RABI":
        return ajustar_rabi(tempos, sinal)
    if seq == "T1":
        return ajustar_exponencial(tempos, sinal, nome="T1", estica=False, unidade=unidade, fator_tempo_total=1.0, fator_conversao=1000.0)
    if seq == "HAN":
        # GeradorSequencias usa time_value como cada intervalo tau: tempo livre total = 2*tau.
        return ajustar_exponencial(tempos, sinal, nome="T2 (Hahn)", estica=True, unidade=unidade, fator_tempo_total=2.0, fator_conversao=1000.0)
    if seq == "RAM":
        return ajustar_ramsey(tempos, sinal, unidade=unidade)
    if seq.startswith("CPMG"):
        # No GeradorSequencias, o tempo total livre é 2*N*pi_pulsos*tempo_varrido.
        try:
            numero_pulsos_pi = int(seq.replace("CPMG", ""))
        except ValueError:
            return {"sucesso": False, "mensagem": "Sequência CPMG sem número de pulsos válido."}
        return ajustar_exponencial(tempos, sinal, nome=f"T2 ({seq})", estica=True, unidade=unidade, fator_tempo_total=2.0 * numero_pulsos_pi, fator_conversao=1000.0)
    if seq == "ESEEM":
        return analisar_eseem(tempos, sinal)

    return {"sucesso": False, "mensagem": f"Sequência '{sequencia}' não reconhecida."}
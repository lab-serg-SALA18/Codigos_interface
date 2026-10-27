"""
Programa pra gerar gráficos manualmente a partir de CSVs de pontos experimentais.
"""

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

def modelo_rabi(t, A, T2s, f_mhz, phi, C, n_exp):
    t = np.asarray(t, dtype=float)
    t = t - 20
    return (A * np.exp(-(t / T2s) ** n_exp)* np.cos(2.0 * np.pi * (f_mhz / 1000.0) * t + phi) + C)

def modelo_exponencial(t, A, T, C):
    return A * np.exp(-t / T) + C

def modelo_exponencial_esticada(t, A, T, C, n):
    return A * np.exp(-(t / T) ** n) + C

def modelo_completo(x, C, A, T, n, B, T_osc, f_mhz, phi):
    x = np.asarray(x, dtype=float)
    fundo = C + A * np.exp(-((x / T) ** n))
    oscilacao = B * np.exp(-x / T_osc) * np.cos(2.0 * np.pi * (f_mhz / 1000.0) * x + phi)
    return fundo + oscilacao

def double_lorentzian(x, y0, A1, x01, gamma1, A2, x02, gamma2):
    return y0 - A1 * (gamma1**2 / ((x - x01)**2 + gamma1**2)) - A2 * (gamma2**2 / ((x - x02)**2 + gamma2**2))

def octuple_lorentzian(x, y0, A1, x01, g1, A2, x02, g2, A3, x03, g3, A4, x04, g4, A5, x05, g5, A6, x06, g6, A7, x07, g7, A8, x08, g8):
    return y0 \
           - A1 * (g1**2 / ((x - x01)**2 + g1**2)) \
           - A2 * (g2**2 / ((x - x02)**2 + g2**2)) \
           - A3 * (g3**2 / ((x - x03)**2 + g3**2)) \
           - A4 * (g4**2 / ((x - x04)**2 + g4**2)) \
           - A5 * (g5**2 / ((x - x05)**2 + g5**2)) \
           - A6 * (g6**2 / ((x - x06)**2 + g6**2)) \
           - A7 * (g7**2 / ((x - x07)**2 + g7**2)) \
           - A8 * (g8**2 / ((x - x08)**2 + g8**2))

def odmr_com_campo(x, y):
    peaks, _ = find_peaks((-y + np.max(y)), distance=15, prominence=0.002)
    p0 = [1.0] # y0
    for xp in x[peaks]:
        p0.extend([0.005, xp, 2.0])
    popt, pcov = curve_fit(octuple_lorentzian, x, y, p0=p0, maxfev=100000)
    return popt

def odmr_sem_campo(x, y):
    peaks, _ = find_peaks((-y + np.max(y)), distance=15, prominence=0.002)
    p0 = [1.0]
    print(x[peaks])
    for xp in x[peaks]:
        p0.extend([0.005, xp, 2.0])
    popt, pcov = curve_fit(double_lorentzian, x, y, p0=p0, maxfev=100000)
    return popt

#arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\Rabi\Dados\18_10_2026\18_10_2026--17_58\pontos_experimentais.csv"
#arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\T1\Dados\22_10_2026\22_10_2026--15_29\pontos_experimentais.csv"
#arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\HAN\Dados\22_09_2026\22_09_2026--21_53\spin_echo_frequencia_analise_corrigida.csv"
#arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\ODMR\Dados\12_09_testecamera\2871MHz_320range_1steps_200avarages_-20dB_2026-09-12_15-32.csv"
#arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\ODMR\Dados-approved\ODMR-without-field\pontos_experimentais.csv"

arquivo = r"C:\Users\lab\Desktop\NV\Joao_pedro\ODMR\Dados-approved\ODMR-without-field\pontos_experimentais.csv"
df = pd.read_csv(arquivo)

plt.figure(figsize=(16, 9))
plt.plot(
    df['mw_freq'],
    df['PL'],
    color='black',
    marker='o',
    linestyle='-',
    linewidth=1.5,
    markersize=5,
    label='Dados',
)

#plt.plot(
#    df['mw_freq'], 
#    double_lorentzian(df['mw_freq'], *odmr_sem_campo(df['mw_freq'], df['PL'])), 
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['mw_freq'], 
#    octuple_lorentzian(df['mw_freq'], *odmr_com_campo(df['mw_freq'], df['PL'])), 
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['tempo_ns'],
#    df['ajuste_completo'],
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['tempo_ns'],
#    df['envelope_suave'],
#    color='blue',
#    linestyle='--',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['tempo_us'],
#    modelo_exponencial(df['tempo_us'], 0.744529016506, 1639.12103707, 0.0177046226926),
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['tempo_us'],
#    modelo_exponencial_esticada(df['tempo_us'], 1.26307120244, 623.050864046, 0.0037757248428, 0.46782360458),
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

#plt.plot(
#    df['tempo_ns'],
#    modelo_rabi(df['tempo_ns'], 0.739667, 260.873, 4.60279, -0.437903, 0.356015, 0.651),
#    color='red',
#    linestyle='-',
#    linewidth=1.5,
#    markersize=5,
#    label='Modelo',
#)

plt.tick_params(direction="out", length=5, width=2, colors="black", labelsize=20)
plt.xlabel("Frequência (MHz)", fontsize=24)
plt.ylabel("Fotoluminescência (normalizada)", fontsize=24)
plt.title("ODMR sem campo", fontsize=24)
plt.grid(alpha=0.3)
plt.legend(fontsize=20)
plt.show()

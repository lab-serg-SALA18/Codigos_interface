"""
Programa pra gerar gráficos manualmente a partir de CSVs de pontos experimentais.
"""

import matplotlib.pyplot as plt
import pandas as pd

arquivo = r'C:\Users\lab\Desktop\NV\Joao_pedro\ODMR\Dados\12_09_testecamera\2871MHz_320range_1steps_200avarages_-20dB_2026-09-12_15-32.csv'
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

plt.tick_params(direction="out", length=5, width=2, colors="black", labelsize=20)
plt.xlabel("Frequência de Micro-ondas (MHz)", fontsize=24)
plt.ylabel("Fotoluminescência (normalizada)", fontsize=24)
plt.title("ODMR com campo", fontsize=24)
plt.grid(alpha=0.3)
plt.legend(fontsize=20)
plt.show()
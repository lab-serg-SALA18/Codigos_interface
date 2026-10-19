"""
Programa pra gerar gráficos manualmente a partir de CSVs de pontos experimentais.
"""

import matplotlib.pyplot as plt
import pandas as pd

arquivo = r'c:\Users\lab\Desktop\NV\Joao_pedro\Rabi\Dados\18_10_2026\18_10_2026--17_58\pontos_experimentais.csv'
df = pd.read_csv(arquivo)

plt.figure(figsize=(16, 9))
plt.plot(
    df['tempo_ns'],
    df['sinal_normalizado_0_1'],
    color='black',
    marker='o',
    linestyle='-',
    linewidth=1.5,
    markersize=5,
    label='Dados',
)
plt.tick_params(direction="out", length=5, width=2, colors="black", labelsize=16)
plt.xlabel("Tempo (ns)", fontsize=18)
plt.ylabel("Fotoluminescência (normalizada)", fontsize=18)
plt.title("RABI - Frequência de Rabi = 4.60 ± 0.003 MHZ | T2* = 260.9 ± 4.6", fontsize=16)
plt.grid(alpha=0.3)
plt.legend(fontsize=14)
plt.show()
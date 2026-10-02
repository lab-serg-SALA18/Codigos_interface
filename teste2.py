import numpy as np
import matplotlib.pyplot as plt

# ------------------------------------------------------------
# 1. PARÂMETROS DA SIMULAÇÃO
# ------------------------------------------------------------
t = np.linspace(0, 1800, 1200)   # Tempo de 0 a 1800 ns
f_mhz = 2.79                     # Frequência de Rabi em MHz
f_ghz = f_mhz / 1000.0           # Frequência em GHz (ciclos/ns)
T2_star = 180.0                  # T2* de Rabi em ns

# Modelo de oscilação amortecida sem deslocamento vertical
# S_bruto(0) = 1.0
sinal_bruto = np.exp(-t / T2_star) * np.cos(2 * np.pi * f_ghz * t)

# ------------------------------------------------------------
# 2. NORMALIZAÇÃO (Primeiro vale em 0.0, Inicio em 1.0)
# ------------------------------------------------------------
# Localização do primeiro vale (aproximadamente t = 1/(2*f))
idx_primeiro_vale = np.argmin(sinal_bruto[:int(len(t) * 0.25)])
s_vale1 = sinal_bruto[idx_primeiro_vale]
s_inicio = sinal_bruto[0]

# Aplicação da escala
sinal_norm = (sinal_bruto - s_vale1) / (s_inicio - s_vale1)

# Envoltória superior e inferior normalizadas
envelope_sup = (np.exp(-t / T2_star) - s_vale1) / (s_inicio - s_vale1)
envelope_inf = (-np.exp(-t / T2_star) - s_vale1) / (s_inicio - s_vale1)
linha_de_base = (-s_vale1) / (s_inicio - s_vale1)

# ------------------------------------------------------------
# 3. CONSTRUÇÃO DO GRÁFICO
# ------------------------------------------------------------
plt.figure(figsize=(10, 5))

# Curva de Rabi normalizada
plt.plot(t, sinal_norm, 'r-', linewidth=2, label=rf'Rabi ($T_2^* = {T2_star}ns$)')

# Envoltória de decaimento
plt.plot(t, envelope_sup, 'r:', alpha=0.6, label='Envelope de decaimento')
plt.plot(t, envelope_inf, 'r:', alpha=0.6)

# Linhas de referência (0.0, 1.0 e nível de equilíbrio)
plt.axhline(1.0, color='black', linestyle='--', alpha=0.4)
plt.axhline(0.0, color='blue', linestyle='--', alpha=0.5, label='Primeiro vale (0.0)')
plt.axhline(linha_de_base, color='gray', linestyle='-.', alpha=0.5, label='Nível assintótico ($t \to \infty$)')

# Destaque do primeiro vale
t_vale1 = t[idx_primeiro_vale]
plt.plot(t_vale1, 0.0, 'bo', markersize=8, label=f'1º Vale em t={t_vale1:.1f} ns')

# Formatação visual
plt.xlabel('Time (ns)', fontsize=12)
plt.ylabel('Photoluminescence (Normalizada)', fontsize=12)
plt.title(r'Oscilação de Rabi Normalizada ($T_2^* = 260\text{ ns}$, 1º Vale = 0.0)', fontsize=13)
plt.ylim(-0.1, 1.1)
plt.grid(alpha=0.3)
plt.legend(fontsize=9, loc='upper right')
plt.tight_layout()

plt.show()
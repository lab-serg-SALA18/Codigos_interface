# -*- coding: utf-8 -*-
"""
Converte arquivos HDF5 (.h5f) de medidas pulsadas em CSV com pontos.
"""

from pathlib import Path
import argparse
import csv
import sys

import h5py
import numpy as np


# ---------------------------------------------------------------------
# CONFIGURAÇÃO PADRÃO — edite para coincidir com a medida realizada
# ---------------------------------------------------------------------
PASTA_DADOS = Path(r"C:\Users\lab\Desktop\NV\Joao_pedro\T1\Dados\21_09_2026\21_09_2026--15_17")
ARQUIVO_ON = PASTA_DADOS / "Cam_MW_on.h5f"
ARQUIVO_OFF = PASTA_DADOS / "Cam_MW_off.h5f"
ARQUIVO_BACKGROUND = PASTA_DADOS / "Cam_background.h5f"
ARQUIVO_SAIDA = PASTA_DADOS / "pontos_experimentais.csv"

TIME_START = 0.0
TIME_STEP = 1.0  # Exemplo de Hahn: 0, 500, 1000, ... ns
USAR_BACKGROUND = True


# ---------------------------------------------------------------------
# FUNÇÕES
# ---------------------------------------------------------------------
def carregar_dataset(caminho):
    """Carrega /dataset de um HDF5 e valida que seja uma matriz 3D."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError("Arquivo não encontrado: {}".format(caminho))

    with h5py.File(str(caminho), "r") as arquivo:
        if "dataset" not in arquivo:
            chaves = list(arquivo.keys())
            raise KeyError(
                "O arquivo '{}' não contém a chave 'dataset'. Chaves encontradas: {}".format(caminho.name, chaves))
        dados = np.asarray(arquivo["dataset"], dtype=np.float64)

    if dados.ndim != 3:
        raise ValueError(
            "Esperava dataset 3D (altura, largura, pontos) em '{}'; forma encontrada: {}".format(caminho.name, dados.shape))
    if dados.shape[2] < 1:
        raise ValueError("O arquivo '{}' não contém pontos de medida.".format(caminho.name))
    if not np.all(np.isfinite(dados)):
        raise ValueError("O arquivo '{}' contém NaN ou infinito.".format(caminho.name))

    return dados


def somar_imagens(dados):
    """Soma os pixels de cada imagem; retorna um valor por ponto temporal."""
    return np.sum(dados, axis=(0, 1), dtype=np.float64)


def normalizar_minmax(valores):
    """Normaliza valores finitos para [0, 1], mantendo inválidos como NaN."""
    valores = np.asarray(valores, dtype=np.float64)
    validos = np.isfinite(valores)
    resultado = np.full_like(valores, np.nan, dtype=np.float64)
    if not np.any(validos):
        return resultado
    minimo = float(np.min(valores[validos]))
    amplitude = float(np.max(valores[validos]) - minimo)
    if not np.isfinite(amplitude) or amplitude <= 0:
        return resultado
    resultado[validos] = (valores[validos] - minimo) / amplitude
    return resultado


def converter(args):
    caminho_on = Path(args.on)
    caminho_off = Path(args.off)
    usar_background = not args.no_background
    caminho_background = Path(args.background) if args.background else None

    on = carregar_dataset(caminho_on)
    off = carregar_dataset(caminho_off)
    if on.shape != off.shape:
        raise ValueError("ON e OFF têm dimensões diferentes: {} vs {}".format(on.shape, off.shape))

    soma_on = somar_imagens(on)
    soma_off = somar_imagens(off)

    if usar_background:
        if caminho_background is None:
            raise ValueError(
                "O background está habilitado, mas nenhum arquivo foi indicado. "
                "Use --background CAMINHO ou --no-background.")
        background = carregar_dataset(caminho_background)
        if background.shape != on.shape:
            raise ValueError(
                "BACKGROUND tem dimensões diferentes: {} vs ON {}".format(background.shape, on.shape))
        soma_background = somar_imagens(background)
        numerador = soma_on - soma_background
        denominador = soma_off - soma_background
    else:
        soma_background = np.full_like(soma_on, np.nan)
        numerador = soma_on
        denominador = soma_off

    eps = np.finfo(np.float64).eps
    razao = np.full_like(soma_on, np.nan, dtype=np.float64)
    mascara_valida = np.abs(denominador) > eps
    razao[mascara_valida] = numerador[mascara_valida] / denominador[mascara_valida]

    sinal_normalizado = normalizar_minmax(razao)
    tempos = args.time_start + np.arange(len(soma_on), dtype=np.float64) * args.time_step

    caminho_saida = Path(args.output)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)

    colunas = [
        "indice_ponto",
        "tempo_ns",
        "soma_pixels_MW_on",
        "soma_pixels_MW_off",
        "soma_pixels_background",
        "razao_sinal",
        "sinal_normalizado_0_1",
    ]

    with caminho_saida.open("w", newline="", encoding="utf-8-sig") as arquivo_csv:
        escritor = csv.writer(arquivo_csv)
        escritor.writerow(colunas)
        for i in range(len(tempos)):
            escritor.writerow([
                i,
                "{:.10g}".format(tempos[i]),
                "{:.12g}".format(soma_on[i]),
                "{:.12g}".format(soma_off[i]),
                "{:.12g}".format(soma_background[i]) if usar_background else "",
                "{:.12g}".format(razao[i]) if np.isfinite(razao[i]) else "",
                "{:.12g}".format(sinal_normalizado[i]) if np.isfinite(sinal_normalizado[i]) else "",
            ])

    print("Conversão concluída.")
    print("ON shape: {} | OFF shape: {}".format(on.shape, off.shape))
    if usar_background:
        print("BACKGROUND shape: {}".format(soma_background.shape))
    print("Número de pontos: {}".format(len(tempos)))
    if len(tempos) > 0:
        print("Eixo temporal: {:.6g} a {:.6g} ns; passo = {:.6g} ns".format(tempos[0], tempos[-1], args.time_step))
    print("CSV salvo em: {}".format(caminho_saida.resolve()))

    invalidos = int(np.count_nonzero(~np.isfinite(razao)))
    if invalidos:
        print("Aviso: {} pontos têm razão indefinida (denominador zero/não finito); "
            "as células correspondentes ficaram vazias no CSV.".format(invalidos),file=sys.stderr,)


def montar_argumentos():
    parser = argparse.ArgumentParser(description="Converte H5F de aquisição em CSV com um registro por ponto temporal.")
    parser.add_argument("--on", default=str(ARQUIVO_ON), help="Caminho do arquivo MW ON.")
    parser.add_argument("--off", default=str(ARQUIVO_OFF), help="Caminho do arquivo MW OFF.")
    parser.add_argument("--background", default=str(ARQUIVO_BACKGROUND),help="Caminho do arquivo de background (usado por padrão).")
    parser.add_argument("--no-background", action="store_true",help="Calcular ON/OFF sem subtrair background.")
    parser.add_argument("--output", default=str(ARQUIVO_SAIDA), help="Caminho do CSV de saída.")
    parser.add_argument("--time-start", type=float, default=TIME_START, help="Tempo do primeiro ponto em ns.")
    parser.add_argument("--time-step", type=float, default=TIME_STEP, help="Passo temporal em ns.")
    args = parser.parse_args()

    if args.time_step <= 0:
        parser.error("--time-step precisa ser positivo.")

    return args


if __name__ == "__main__":
    try:
        converter(montar_argumentos())
    except Exception as exc:
        print("ERRO: {}".format(exc), file=sys.stderr)
        sys.exit(1)

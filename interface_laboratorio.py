# -*- coding: utf-8 -*-
"""
Interface gráfica do laboratório NV — painel único para ODMR, Rabi, T1,
Hahn Echo, Ramsey, CPMG (2/4/8) e ESEEM.

ESTE É O ARQUIVO QUE VOCÊ RODA, com o Python 3.6 (o mesmo da câmera):

    python interface_laboratorio.py

Fluxo:
  - ODMR: roda direto neste processo (só precisa da câmera + gerador de MW).
    Ativa laser/câmera/chave de MW automaticamente (sem precisar do software
    da SpinCore) e mostra a ressonância de maior contraste ao final.
  - Rabi / T1 / HAN / RAM / CPMGx / ESEEM: esta interface escreve o
    ParametrosSequencias.py com os parâmetros escolhidos na tela, sobe o
    NVserver.py num subprocesso do Python 3.11 (substituindo o segundo
    terminal manual), espera as duas portas ficarem prontas, roda a
    aquisição e no final já mostra o T1/T2/T2*/frequência de Rabi ajustado.

Veja LEIA-ME.md para instruções de instalação e configuração.
"""

import os
import threading
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg

import pygame
import textwrap

import configuracoes_lab as cfg
import servidor_manager
import experimento_sequencia
import odmr_controller

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ======================================================================= #
#                                   LAYOUT
# ======================================================================= #

LARGURA_JANELA, ALTURA_JANELA = 1320, 840
LARGURA_SIDEBAR = 260

FORM_X_ROTULO = LARGURA_SIDEBAR + 24     # 284
FORM_X_CAMPO = LARGURA_SIDEBAR + 230     # 490
FORM_LARGURA_CAMPO = 200
FORM_Y_INICIO = 168
FORM_ESPACO = 38
FORM_MAX_CAMPOS = 11  # o maior esquema (experimentos pulsados têm 11 campos)

BOTOES_Y = FORM_Y_INICIO + FORM_MAX_CAMPOS * FORM_ESPACO + 20   # 606

STATUS_X = FORM_X_ROTULO
STATUS_Y = BOTOES_Y + 60                                        # 666
STATUS_LARGURA = 500
STATUS_ALTURA = 150

RESULTADO_X = LARGURA_SIDEBAR + 550                              # 810
RESULTADO_Y = 90

# ======================================================================= #
# VISUAL
# ======================================================================= #

CORES = {
    "fundo": (18, 20, 28),
    "painel": (28, 31, 43),
    "painel_claro": (36, 40, 54),
    "borda": (54, 59, 78),
    "texto": (229, 231, 235),
    "texto_secundario": (156, 163, 175),
    "destaque": (79, 209, 197),
    "sucesso": (52, 211, 153),
    "erro": (248, 113, 113),
    "aviso": (246, 173, 85),
    "campo_fundo": (22, 24, 33),
    "desabilitado": (70, 74, 90),
}

CORES_EXPERIMENTOS = {
    "ODMR": (99, 179, 237),
    "RABI": (129, 230, 217),
    "T1": (246, 173, 85),
    "HAN": (245, 101, 101),
    "RAM": (183, 148, 246),
    "CPMG2": (159, 122, 234),
    "CPMG4": (128, 90, 213),
    "CPMG8": (107, 70, 193),
    "ESEEM": (72, 187, 120),
}

_NAO_INFORMADO = object()


def _clarear(cor, quantidade=20):
    return tuple(min(255, c + quantidade) for c in cor)


def _formatar_numero(valor):
    if isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, int):
        return str(valor)
    return f"{valor:.6g}"


def _formatar_tempo(segundos):
    if segundos is None or not isinstance(segundos, (int, float)) or segundos < 0:
        return "--:--"
    segundos = int(segundos)
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas:02d}:{minutos:02d}:{segundos:02d}"
    return f"{minutos:02d}:{segundos:02d}"


def figura_para_surface(fig, largura_max=440):
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    largura, altura = canvas.get_width_height()
    buffer = canvas.buffer_rgba()
    surface = pygame.image.frombuffer(buffer, (largura, altura), "RGBA").convert_alpha()
    if largura > largura_max:
        fator = largura_max / largura
        surface = pygame.transform.smoothscale(surface, (int(largura * fator), int(altura * fator)))
    return surface


# ======================================================================= #
# WIDGETS
# ======================================================================= #

class CampoTexto:
    def __init__(self, rect, valor_inicial="", numerico=True):
        self.rect = pygame.Rect(rect)
        self.texto = _formatar_numero(valor_inicial) if numerico else str(valor_inicial)
        self.ativo = False
        self.numerico = numerico

    def lidar_evento(self, evento):
        if evento.type == pygame.MOUSEBUTTONDOWN:
            self.ativo = self.rect.collidepoint(evento.pos)
        elif evento.type == pygame.KEYDOWN and self.ativo:
            if evento.key == pygame.K_BACKSPACE:
                self.texto = self.texto[:-1]
            elif evento.key in (pygame.K_RETURN, pygame.K_TAB):
                self.ativo = False
            else:
                ch = evento.unicode
                if self.numerico:
                    if ch.isdigit() or (ch in "-." and ch not in self.texto):
                        self.texto += ch
                elif ch.isprintable():
                    self.texto += ch

    def valor_float(self, padrao=0.0):
        try:
            return float(self.texto)
        except ValueError:
            return padrao

    def desenhar(self, tela, fonte):
        cor_borda = CORES["destaque"] if self.ativo else CORES["borda"]
        pygame.draw.rect(tela, CORES["campo_fundo"], self.rect, border_radius=8)
        pygame.draw.rect(tela, cor_borda, self.rect, width=2, border_radius=8)
        render = fonte.render(self.texto, True, CORES["texto"])
        tela.blit(render, (self.rect.x + 10, self.rect.y + (self.rect.height - render.get_height()) // 2))


class Botao:
    def __init__(self, rect, texto, ao_clicar, cor=None, cor_texto=None):
        self.rect = pygame.Rect(rect)
        self.texto = texto
        self.ao_clicar = ao_clicar
        self.cor = cor or CORES["destaque"]
        self.cor_texto = cor_texto if cor_texto is not None else (18, 20, 28)
        self.habilitado = True

    def lidar_evento(self, evento):
        if not self.habilitado:
            return
        if evento.type == pygame.MOUSEBUTTONDOWN and evento.button == 1:
            if self.rect.collidepoint(evento.pos):
                self.ao_clicar()

    def desenhar(self, tela, fonte):
        cor = self.cor if self.habilitado else CORES["desabilitado"]
        mouse = pygame.mouse.get_pos()
        if self.habilitado and self.rect.collidepoint(mouse):
            cor = _clarear(cor, 20)
        pygame.draw.rect(tela, cor, self.rect, border_radius=10)
        cor_texto = self.cor_texto if self.habilitado else CORES["texto_secundario"]
        render = fonte.render(self.texto, True, cor_texto)
        pos = render.get_rect(center=self.rect.center)
        tela.blit(render, pos)


class Alternador:
    def __init__(self, rect, valor_inicial):
        self.rect = pygame.Rect(rect)
        self.valor = bool(valor_inicial)

    def lidar_evento(self, evento):
        if evento.type == pygame.MOUSEBUTTONDOWN and evento.button == 1:
            if self.rect.collidepoint(evento.pos):
                self.valor = not self.valor

    def desenhar(self, tela, fonte):
        cor = CORES["sucesso"] if self.valor else CORES["desabilitado"]
        pygame.draw.rect(tela, cor, self.rect, border_radius=self.rect.height // 2)
        cx = self.rect.right - self.rect.height // 2 if self.valor else self.rect.left + self.rect.height // 2
        pygame.draw.circle(tela, (255, 255, 255), (cx, self.rect.centery), self.rect.height // 2 - 3)
        texto = "Sim" if self.valor else "Não"
        render = fonte.render(texto, True, CORES["texto_secundario"])
        tela.blit(render, (self.rect.right + 10, self.rect.centery - render.get_height() // 2))


# ======================================================================= #
# ESTADO DE EXECUÇÃO (compartilhado com a thread de fundo)
# ======================================================================= #

class EstadoExecucao:
    def __init__(self):
        self.lock = threading.Lock()
        self.rodando = False
        self.progresso = 0.0
        self.mensagem = ""
        self.log = []
        self.resultado = None
        self.erro = None
        self.processo_servidor = None
        self.experimento_atual = None
        self.tempo_inicio = None
        self.tempo_decorrido = 0.0
        self.tempo_restante = None

    def iniciar_cronometro(self):
        with self.lock:
            self.tempo_inicio = time.monotonic()
            self.tempo_decorrido = 0.0
            self.tempo_restante = None

    def registrar(
        self,
        mensagem=None,
        progresso=None,
        tempo_decorrido=_NAO_INFORMADO,
        tempo_restante=_NAO_INFORMADO,
    ):
        with self.lock:
            if mensagem:
                self.log.append(mensagem)
                self.log = self.log[-20:]
                self.mensagem = mensagem

            # Nas sequências pulsadas esses valores são calculados pelo tqdm,
            # que usa uma taxa média suavizada. Para outras operações, só
            # mantemos o cronômetro de tempo decorrido e não inventamos uma ETA.
            if tempo_decorrido is not _NAO_INFORMADO:
                self.tempo_decorrido = max(0.0, float(tempo_decorrido))
            elif self.tempo_inicio is not None:
                self.tempo_decorrido = time.monotonic() - self.tempo_inicio

            if progresso is not None:
                self.progresso = max(0.0, min(1.0, float(progresso)))
            if tempo_restante is not _NAO_INFORMADO:
                self.tempo_restante = tempo_restante
            elif progresso is not None:
                self.tempo_restante = None


# ======================================================================= #
# INTERFACE PRINCIPAL
# ======================================================================= #

class InterfaceLaboratorio:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Laboratório NV — Painel de Controle")
        self.tela = pygame.display.set_mode((LARGURA_JANELA, ALTURA_JANELA))

        self.fonte_titulo = pygame.font.SysFont("segoeui", 24, bold=True)
        self.fonte_subtitulo = pygame.font.SysFont("segoeui", 14)
        self.fonte_normal = pygame.font.SysFont("segoeui", 14)
        self.fonte_pequena = pygame.font.SysFont("segoeui", 12)
        self.fonte_resultado = pygame.font.SysFont("segoeui", 22, bold=True)
        self.fonte_mono = pygame.font.SysFont("consolas", 12)

        self.config = cfg.carregar_configuracao()
        self.ordem_experimentos = list(cfg.EXPERIMENTOS.keys())

        self.estado = EstadoExecucao()
        self.thread_execucao = None
        self.campos = {}
        self.botoes_sidebar = []
        self.mensagem_validacao = ""
        self._unidade_tempo_atual = ""

        self._construir_sidebar()
        self.experimento_selecionado = self.ordem_experimentos[0]
        self.construir_formulario(self.experimento_selecionado)

        self.botao_executar = Botao(
            (FORM_X_ROTULO, BOTOES_Y, 150, 42), "Executar", self.iniciar_execucao, cor=CORES["destaque"]
        )
        self.botao_parar = Botao(
            (FORM_X_ROTULO + 164, BOTOES_Y, 110, 42), "Parar", self.parar_execucao,
            cor=CORES["erro"], cor_texto=(255, 255, 255),
        )
        self.botao_ver_grafico = Botao(
            (RESULTADO_X, ALTURA_JANELA - 64, 240, 34), "Ver gráfico completo", self.abrir_grafico,
            cor=CORES["painel_claro"], cor_texto=CORES["texto"],
        )

    # ------------------------------------------------------------- #
    # CONSTRUÇÃO DE LAYOUT
    # ------------------------------------------------------------- #

    def _construir_sidebar(self):
        y = 90
        self.botoes_sidebar = []
        for chave in self.ordem_experimentos:
            rect = pygame.Rect(16, y, LARGURA_SIDEBAR - 32, 64)
            self.botoes_sidebar.append((chave, rect))
            y += 74

    def selecionar_experimento(self, chave):
        if self.estado.rodando:
            return
        self.experimento_selecionado = chave
        self.mensagem_validacao = ""
        self.construir_formulario(chave)
        self.estado = EstadoExecucao()

    def construir_formulario(self, sequencia):
        schema = cfg.EXPERIMENTOS[sequencia]["parametros_padrao"]
        valores_atuais = self.config[sequencia]

        self.campos = {}
        y = FORM_Y_INICIO
        for chave, padrao in schema.items():
            valor = valores_atuais.get(chave, padrao)
            if isinstance(padrao, bool):
                self.campos[chave] = Alternador((FORM_X_CAMPO, y + 3, 46, 24), valor)
            else:
                self.campos[chave] = CampoTexto((FORM_X_CAMPO, y, FORM_LARGURA_CAMPO, 32), valor, numerico=True)
            y += FORM_ESPACO

        self._unidade_tempo_atual = cfg.EXPERIMENTOS[sequencia].get("unidade_tempo", "")

    # ------------------------------------------------------------- #
    # EXECUÇÃO (threads de fundo)
    # ------------------------------------------------------------- #

    def _ler_formulario(self):
        schema = cfg.EXPERIMENTOS[self.experimento_selecionado]["parametros_padrao"]
        valores = {}
        for chave, padrao in schema.items():
            widget = self.campos[chave]
            if isinstance(widget, Alternador):
                valores[chave] = widget.valor
            elif isinstance(padrao, int):
                valores[chave] = int(round(widget.valor_float(padrao)))
            else:
                valores[chave] = widget.valor_float(padrao)
        return valores

    def iniciar_execucao(self):
        if self.estado.rodando:
            return

        sequencia = self.experimento_selecionado
        params = self._ler_formulario()

        if sequencia != "ODMR":
            if params["time_stop"] <= params["time_start"]:
                self.mensagem_validacao = "O tempo final deve ser maior que o inicial."
                return
            if params["time_step"] <= 0:
                self.mensagem_validacao = "O passo de tempo deve ser maior que zero."
                return
        else:
            if params["mw_freq_step"] <= 0:
                self.mensagem_validacao = "O passo de frequência deve ser maior que zero."
                return

        self.mensagem_validacao = ""
        self.config[sequencia] = params
        cfg.salvar_configuracao(self.config)

        self.estado = EstadoExecucao()
        self.estado.rodando = True
        self.estado.iniciar_cronometro()

        if sequencia == "ODMR":
            self.thread_execucao = threading.Thread(target=self._executar_odmr_thread, args=(params,), daemon=True)
        else:
            self.thread_execucao = threading.Thread(
                target=self._executar_sequencia_thread, args=(sequencia, params), daemon=True)
        self.thread_execucao.start()

    def parar_execucao(self):
        if not self.estado.rodando:
            return
        if self.estado.experimento_atual is not None:
            self.estado.experimento_atual.cancelado = True
        self.estado.registrar(mensagem="Cancelando... aguardando o passo atual terminar.")

    def _finalizar_resultado(self, estado, resultado, erro=None):
        if resultado and resultado.get("figura") is not None:
            try:
                resultado["preview_surface"] = figura_para_surface(resultado["figura"])
            except Exception:
                resultado["preview_surface"] = None
            finally:
                plt.close(resultado["figura"])
                resultado["figura"] = None
        with estado.lock:
            estado.resultado = resultado
            estado.erro = erro
            estado.rodando = False

    def _executar_odmr_thread(self, params):
        estado = self.estado
        try:
            controlador = odmr_controller.ODMRController(
                shutter_time_ms=params["shutter_time_ms"],
                mw_freq_center=params["mw_freq_center"],
                mw_freq_range=params["mw_freq_range"],
                mw_freq_step=params["mw_freq_step"],
                naverage=int(params["naverage"]),
                mw_power=params["mw_power"],
                progress_callback=estado.registrar,
            )
            with estado.lock:
                estado.experimento_atual = controlador
            resultado = controlador.executar()
            self._finalizar_resultado(estado, resultado)
        except Exception as e:
            print("Erro detalhado no ODMR:", repr(e))
            self._finalizar_resultado(estado, None, erro=str(e))


    def _executar_sequencia_thread(self, sequencia, params):
        estado = self.estado
        processo = None
        try:
            estado.registrar(mensagem="Escrevendo parâmetros da sequência...")
            servidor_manager.escrever_parametros_sequencia(sequencia, params)

            estado.registrar(mensagem="Iniciando servidor NVserver.py (Python 3.11)...")
            processo = servidor_manager.iniciar_servidor_nv(log_callback=estado.registrar)
            with estado.lock:
                estado.processo_servidor = processo

            if not servidor_manager.aguardar_portas_prontas(timeout=20):
                raise RuntimeError(
                    "O servidor NV não respondeu a tempo. Confira PYTHON_311_EXE e "
                    "PASTA_PROJETO em configuracoes_lab.py, e veja o log acima."
                )

            estado.registrar(mensagem="Servidor pronto. Iniciando aquisição...")
            infos = (
                f"{params['frequency']}mhz_{params['mw_power']}dbm_{params['images_average']}averages_"
                f"{params['time_start']}_{params['time_stop']}_{int(params['laser_time_us'] * 1000)}ns_"
                f"{params['measure_loops']}loops_{sequencia}"
            )
            time_array = range(int(params["time_start"]), int(params["time_stop"] + params["time_step"]), int(params["time_step"]))

            if sequencia == "T1":
                dynamic_shutter = False
            if sequencia == "HAN":
                dynamic_shutter = True
            if "dynamic_shutter" in params:
                dynamic_shutter = params["dynamic_shutter"]


            exp = experimento_sequencia.ExperimentoSequencia(
                sequencia=sequencia,
                frequency=params["frequency"],
                time_array=time_array,
                mw_power=params["mw_power"],
                average=int(params["images_average"]),
                infos=infos,
                dynamic_shutter=dynamic_shutter,
                backend_photo_on=params["backend_photo_on"],
                plotar_fit=params.get("plotar_fit", True),
                progress_callback=estado.registrar,
            )
            with estado.lock:
                estado.experimento_atual = exp
            resultado = exp.executar()
            self._finalizar_resultado(estado, resultado)

        except Exception as e:
            print("Erro detalhado nas threads:", repr(e))
            self._finalizar_resultado(estado, None, erro=str(e))
        finally:
            if processo is not None:
                servidor_manager.parar_servidor_nv(processo)
                with estado.lock:
                    estado.processo_servidor = None

    def abrir_grafico(self):
        resultado = self.estado.resultado
        if not resultado or not resultado.get("caminho_grafico"):
            return
        caminho = resultado["caminho_grafico"]
        try:
            if hasattr(os, "startfile"):
                os.startfile(caminho)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", caminho])
        except Exception as e:
            self.estado.registrar(mensagem=f"Não consegui abrir o gráfico: {e}")

    # ------------------------------------------------------------- #
    # EVENTOS
    # ------------------------------------------------------------- #

    def _processar_evento(self, evento):
        if evento.type == pygame.MOUSEBUTTONDOWN and evento.button == 1:
            for chave, rect in self.botoes_sidebar:
                if rect.collidepoint(evento.pos):
                    self.selecionar_experimento(chave)
                    return

        for widget in self.campos.values():
            widget.lidar_evento(evento)

        self.botao_executar.lidar_evento(evento)
        self.botao_parar.lidar_evento(evento)
        self.botao_ver_grafico.lidar_evento(evento)

    # ------------------------------------------------------------- #
    # DESENHO
    # ------------------------------------------------------------- #

    def desenhar(self):
        self.botao_executar.habilitado = not self.estado.rodando
        self.botao_parar.habilitado = self.estado.rodando
        self.botao_ver_grafico.habilitado = bool(
            self.estado.resultado and self.estado.resultado.get("caminho_grafico")
        )

        self.tela.fill(CORES["fundo"])
        self._desenhar_topo()
        self._desenhar_sidebar()
        self._desenhar_formulario()
        self._desenhar_status()
        self._desenhar_resultado()

    def _desenhar_topo(self):
        pygame.draw.rect(self.tela, CORES["painel"], (0, 0, LARGURA_JANELA, 70))
        titulo = self.fonte_titulo.render("Laboratório NV — Painel de Controle", True, CORES["texto"])
        self.tela.blit(titulo, (24, 20))

        if self.estado.rodando:
            cor_status, texto_status = CORES["destaque"], "Executando..."
        elif self.estado.erro:
            cor_status, texto_status = CORES["erro"], "Erro"
        elif self.estado.resultado:
            cor_status, texto_status = CORES["sucesso"], "Concluído"
        else:
            cor_status, texto_status = CORES["texto_secundario"], "Pronto"

        pygame.draw.circle(self.tela, cor_status, (LARGURA_JANELA - 160, 35), 6)
        render = self.fonte_normal.render(texto_status, True, cor_status)
        self.tela.blit(render, (LARGURA_JANELA - 145, 27))

    def _desenhar_sidebar(self):
        pygame.draw.rect(self.tela, CORES["painel"], (0, 70, LARGURA_SIDEBAR, ALTURA_JANELA - 70))
        mouse = pygame.mouse.get_pos()

        for chave, rect in self.botoes_sidebar:
            selecionado = chave == self.experimento_selecionado
            cor_badge = CORES_EXPERIMENTOS.get(chave, CORES["destaque"])

            if selecionado:
                pygame.draw.rect(self.tela, CORES["painel_claro"], rect, border_radius=12)
                pygame.draw.rect(self.tela, cor_badge, rect, width=2, border_radius=12)
            elif rect.collidepoint(mouse):
                pygame.draw.rect(self.tela, CORES["painel_claro"], rect, border_radius=12)
            else:
                pygame.draw.rect(self.tela, CORES["painel"], rect, border_radius=12)

            pygame.draw.circle(self.tela, cor_badge, (rect.x + 30, rect.centery), 16)
            iniciais = cfg.EXPERIMENTOS[chave]["nome_exibicao"][:2].upper()
            render_ini = self.fonte_pequena.render(iniciais, True, (18, 20, 28))
            self.tela.blit(render_ini, render_ini.get_rect(center=(rect.x + 30, rect.centery)))

            nome = cfg.EXPERIMENTOS[chave]["nome_exibicao"]
            render_nome = self.fonte_normal.render(nome, True, CORES["texto"])
            self.tela.blit(render_nome, (rect.x + 58, rect.y + 14))

            desc = cfg.EXPERIMENTOS[chave]["descricao"]
            if len(desc) > 30:
                desc = desc[:29] + "..."
            render_desc = self.fonte_pequena.render(desc, True, CORES["texto_secundario"])
            self.tela.blit(render_desc, (rect.x + 58, rect.y + 36))

    def _desenhar_formulario(self):
        x = FORM_X_ROTULO
        nome_exp = cfg.EXPERIMENTOS[self.experimento_selecionado]["nome_exibicao"]
        titulo = self.fonte_titulo.render(nome_exp, True, CORES["texto"])
        self.tela.blit(titulo, (x, 90))

        grandeza = cfg.EXPERIMENTOS[self.experimento_selecionado].get("grandeza")
        if grandeza:
            sub = self.fonte_subtitulo.render(f"Ajuste automático: {grandeza}", True, CORES["destaque"])
            self.tela.blit(sub, (x, 124))

        schema = cfg.EXPERIMENTOS[self.experimento_selecionado]["parametros_padrao"]
        y = FORM_Y_INICIO
        for chave in schema:
            rotulo = cfg.ROTULOS_PARAMETROS.get(chave, chave)
            if chave in ("time_start", "time_stop", "time_step") and self._unidade_tempo_atual:
                rotulo += f" ({self._unidade_tempo_atual})"
            render_rotulo = self.fonte_normal.render(rotulo, True, CORES["texto_secundario"])
            self.tela.blit(render_rotulo, (x, y + 6))
            self.campos[chave].desenhar(self.tela, self.fonte_normal)
            y += FORM_ESPACO

        if self.mensagem_validacao:
            render = self.fonte_pequena.render(self.mensagem_validacao, True, CORES["erro"])
            self.tela.blit(render, (x, BOTOES_Y - 20))

        self.botao_executar.desenhar(self.tela, self.fonte_normal)
        self.botao_parar.desenhar(self.tela, self.fonte_normal)

    def _desenhar_status(self):
        x, y = STATUS_X, STATUS_Y
        largura, altura = STATUS_LARGURA, STATUS_ALTURA

        pygame.draw.rect(self.tela, CORES["painel"], (x, y, largura, altura), border_radius=12)

        barra_rect = pygame.Rect(x + 16, y + 16, largura - 32, 14)
        pygame.draw.rect(self.tela, CORES["campo_fundo"], barra_rect, border_radius=7)
        progresso = max(0.0, min(1.0, self.estado.progresso))
        if progresso > 0:
            preenchido = pygame.Rect(barra_rect.x, barra_rect.y, int(barra_rect.width * progresso), barra_rect.height)
            pygame.draw.rect(self.tela, CORES["destaque"], preenchido, border_radius=7)
        percentual = self.fonte_pequena.render(f"{progresso * 100:.0f}%", True, CORES["texto_secundario"])
        self.tela.blit(percentual, (x + largura - 46, y + 34))

        with self.estado.lock:
            decorrido = self.estado.tempo_decorrido
            restante = self.estado.tempo_restante

        texto_tempo = (
            f"Decorrido: {_formatar_tempo(decorrido)}    "
            f"Restante: {_formatar_tempo(restante)}"
        )
        render_tempo = self.fonte_pequena.render(
            texto_tempo, True, CORES["texto_secundario"]
        )
        self.tela.blit(render_tempo, (x + 16, y + 34))

        y_log = y + 62
        for linha in list(self.estado.log)[-4:]:
            if len(linha) > 62:
                linha = linha[:61] + "..."
            render = self.fonte_mono.render(linha, True, CORES["texto_secundario"])
            self.tela.blit(render, (x + 16, y_log))
            y_log += 16

        if self.estado.erro:
            erro_texto = self.estado.erro
            if len(erro_texto) > 68:
                erro_texto = erro_texto[:67] + "..."
            render = self.fonte_pequena.render(f"Erro no estado: {repr(erro_texto)}", True, CORES["erro"])
            self.tela.blit(render, (x + 16, y + altura - 22))

    def _desenhar_resultado(self):
        x, y = RESULTADO_X, RESULTADO_Y
        largura = LARGURA_JANELA - x - 24
        altura = ALTURA_JANELA - y - 24

        pygame.draw.rect(self.tela, CORES["painel"], (x, y, largura, altura), border_radius=14)
        titulo = self.fonte_subtitulo.render("Resultado", True, CORES["texto_secundario"])
        self.tela.blit(titulo, (x + 20, y + 16))

        resultado = self.estado.resultado
        if resultado is None:
            msg = "Medindo..." if self.estado.rodando else "Nenhuma medida rodada ainda."
            render = self.fonte_normal.render(msg, True, CORES["texto_secundario"])
            self.tela.blit(render, (x + 20, y + 50))
            self.botao_ver_grafico.desenhar(self.tela, self.fonte_pequena)
            return

        y_cursor = y + 50

        if not resultado.get("sucesso"):
            mensagem_erro = resultado.get("mensagem", "Falhou.")
            # Quebra o texto em partes de 60 caracteres para caber na caixa
            linhas_erro = textwrap.wrap(mensagem_erro, width=60)
            
            for linha in linhas_erro:
                render = self.fonte_normal.render(linha, True, CORES["erro"])
                self.tela.blit(render, (x + 20, y_cursor))
                y_cursor += 20  # Pula para a próxima linha

        elif "melhor_ressonancia" in resultado:
            melhor = resultado.get("melhor_ressonancia")
            if melhor:
                render1 = self.fonte_resultado.render(f"{melhor['freq']:.2f} MHz", True, CORES["sucesso"])
                render2 = self.fonte_normal.render(
                    f"contraste {melhor['contraste'] * 100:.1f}%", True, CORES["texto_secundario"]
                )
                self.tela.blit(render1, (x + 20, y_cursor))
                self.tela.blit(render2, (x + 20, y_cursor + 34))
                y_cursor += 66

                outras = resultado.get("ressonancias", [])[1:6]
                if outras:
                    render_o = self.fonte_pequena.render("Outros vales encontrados:", True, CORES["texto_secundario"])
                    self.tela.blit(render_o, (x + 20, y_cursor))
                    y_cursor += 18
                    for r in outras:
                        linha = f"  {r['freq']:.2f} MHz  —  contraste {r['contraste'] * 100:.1f}%"
                        render_l = self.fonte_pequena.render(linha, True, CORES["texto_secundario"])
                        self.tela.blit(render_l, (x + 20, y_cursor))
                        y_cursor += 16
            else:
                render = self.fonte_normal.render("Nenhum vale claro encontrado.", True, CORES["aviso"])
                self.tela.blit(render, (x + 20, y_cursor))

        else:
            ajuste = resultado.get("ajuste", {})
            if ajuste.get("sucesso"):
                unidade = ajuste.get("unidade", "")
                render1 = self.fonte_resultado.render(f"{ajuste['valor']:.4g} {unidade}", True, CORES["sucesso"])
                render2 = self.fonte_normal.render(ajuste.get("rotulo", ""), True, CORES["texto_secundario"])
                self.tela.blit(render1, (x + 20, y_cursor))
                self.tela.blit(render2, (x + 20, y_cursor + 34))
                y_cursor += 66

                incerteza = ajuste.get("incerteza", float("nan"))
                r2 = ajuste.get("r2", float("nan"))
                if r2 == r2:
                    render_r2 = self.fonte_pequena.render(
                        f"R² do ajuste: {r2:.5f}",
                        True,
                        CORES["texto_secundario"]
                    )

                    self.tela.blit(
                        render_r2,
                        (x + 20, y_cursor)
                    )

                    y_cursor += 20
                if incerteza == incerteza:  # descarta NaN
                    render_i = self.fonte_pequena.render(
                        f"incerteza do ajuste: ± {incerteza:.2g} {unidade}", True, CORES["texto_secundario"]
                    )
                    self.tela.blit(render_i, (x + 20, y_cursor))
                    y_cursor += 20

                if "frequencia_franja" in ajuste:
                    # O detuning gerado na analise_automatica_3 é extraído aqui
                    detuning_mhz = ajuste["frequencia_franja"] * 1000.0
                    erro_detuning = ajuste.get("frequencia_franja_incerteza", float("nan")) * 1000.0
                    
                    texto_detuning = f"Detuning (Δf): {detuning_mhz:.3f} MHz"
                    if erro_detuning == erro_detuning:  # descarta NaN
                        texto_detuning += f" ± {erro_detuning:.3f} MHz"
                        
                    render_detuning = self.fonte_pequena.render(
                        texto_detuning, True, CORES["destaque"]
                    )
                    self.tela.blit(render_detuning, (x + 20, y_cursor))
                    y_cursor += 20

                if "pulso_pi_estimado_ns" in ajuste:
                    render_p = self.fonte_pequena.render(
                        f"pulso pi estimado: {ajuste['pulso_pi_estimado_ns']:.1f} ns",
                        True, CORES["texto_secundario"],
                    )
                    self.tela.blit(render_p, (x + 20, y_cursor))
                    y_cursor += 20

                if "t2_star_rabi_ns" in ajuste:
                    t2 = ajuste["t2_star_rabi_ns"]
                    erro_t2 = ajuste.get("t2_star_rabi_incerteza_ns", float("nan"))
                    texto_t2 = f"T2* (Rabi): {t2:.1f} ns"
                    if erro_t2 == erro_t2:  # descarta NaN
                        texto_t2 += f" ± {erro_t2:.1f} ns"
                    render_t2 = self.fonte_pequena.render(texto_t2, True, CORES["texto_secundario"])
                    self.tela.blit(render_t2, (x + 20, y_cursor))
                    y_cursor += 20
            else:
                render = self.fonte_normal.render(
                    ajuste.get("mensagem", "Ajuste automático não disponível."), True, CORES["aviso"]
                )
                self.tela.blit(render, (x + 20, y_cursor))

        preview = resultado.get("preview_surface")
        if preview is not None:
            pos_x = x + (largura - preview.get_width()) // 2
            pos_y = y + 210
            if pos_y + preview.get_height() < y + altura - 50:
                self.tela.blit(preview, (pos_x, pos_y))

        self.botao_ver_grafico.desenhar(self.tela, self.fonte_pequena)

    # ------------------------------------------------------------- #
    # LOOP PRINCIPAL
    # ------------------------------------------------------------- #

    def loop_principal(self):
        relogio = pygame.time.Clock()
        rodando_app = True
        while rodando_app:
            for evento in pygame.event.get():
                if evento.type == pygame.QUIT:
                    rodando_app = False
                else:
                    self._processar_evento(evento)

            self.desenhar()
            pygame.display.flip()
            relogio.tick(30)

        self._finalizar()
        pygame.quit()

    def _finalizar(self):
        if self.estado.rodando:
            if self.estado.experimento_atual is not None:
                self.estado.experimento_atual.cancelado = True
            if self.estado.processo_servidor is not None:
                servidor_manager.parar_servidor_nv(self.estado.processo_servidor)


if __name__ == "__main__":
    app = InterfaceLaboratorio()
    app.loop_principal()
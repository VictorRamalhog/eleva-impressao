"""A janela. É este arquivo que vira ``Eleva Impressão.exe``.

O programa é um servidor local mais uma janela que o mostra. Duas decisões que
evitam dor de cabeça no dia a dia:

**Porta sorteada pelo sistema.** Nada de fixar 8000 — a UI do Eleva já usa essa
porta, e duas coisas disputando a mesma porta é um erro que só aparece quando as
duas estão abertas. Aqui o sistema escolhe uma porta livre e a janela recebe o
endereço pronto.

**Só no loopback.** O servidor escuta em 127.0.0.1. Este programa fala com a
impressora e escreve arquivos; não tem por que estar visível na rede do galpão.

**A janela tem três planos.** Nativa (pywebview), navegador em modo aplicativo
(``--app``, sem barra de endereço) e, por último, uma aba comum com um aviso
explicando o que houve. Abrir de um jeito pior é melhor do que não abrir — e
como o programa é a única coisa que fala com a impressora, ele não pode ficar
inacessível porque um backend gráfico faltou na máquina.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import closing, suppress
from pathlib import Path

import uvicorn

from eleva_impressao import formatos
from eleva_impressao.app import TITULO, VERSAO, criar_app

ENDERECO = "127.0.0.1"
JANELA_LARGURA = 1360
JANELA_ALTURA = 900


def porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as tomada:
        tomada.bind((ENDERECO, 0))
        return int(tomada.getsockname()[1])


class Servidor:
    """Uvicorn numa thread, com um jeito honesto de saber que já subiu."""

    def __init__(self, porta: int) -> None:
        configuracao = uvicorn.Config(
            criar_app(),
            host=ENDERECO,
            port=porta,
            log_level="warning",
            access_log=False,
            # ``log_config=None`` desliga a configuração de log colorida do
            # uvicorn. Não é preferência estética: o formatador dele chama
            # ``sys.stdout.isatty()`` no construtor, e num programa de janela
            # (o ``.exe``) ``sys.stdout`` é ``None`` — o servidor nem chegava a
            # subir, morria montando o logger.
            log_config=None,
        )
        self.servidor = uvicorn.Server(configuracao)
        self.porta = porta
        self.thread = threading.Thread(target=self.servidor.run, daemon=True, name="eleva-impressao")

    @property
    def url(self) -> str:
        return f"http://{ENDERECO}:{self.porta}/"

    def subir(self, espera_s: float = 20.0) -> bool:
        self.thread.start()
        limite = time.monotonic() + espera_s
        while time.monotonic() < limite:
            if getattr(self.servidor, "started", False):
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def descer(self) -> None:
        self.servidor.should_exit = True
        self.thread.join(timeout=5)


def abrir_janela(url: str) -> bool:
    """Janela nativa via pywebview. Devolve ``False`` se não houver como.

    Duas armadilhas já vividas aqui:

    - o pywebview só falha na hora de **criar** a janela, não no ``import`` —
      por isso o ``try`` envolve o ``start()`` também;
    - dentro do ``.exe``, o pythonnet escolhia o .NET Core, onde não existe
      ``System.Windows.Forms``, e a janela morria com "Could not load file or
      assembly". ``PYTHONNET_RUNTIME=netfx`` manda usar o .NET Framework, que
      vem no Windows e tem o WinForms. Precisa estar no ambiente **antes** do
      primeiro ``import webview``.
    """
    os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
    try:
        import webview
    except ImportError:
        return False

    try:
        webview.create_window(
            f"{TITULO} {VERSAO}",
            url,
            width=JANELA_LARGURA,
            height=JANELA_ALTURA,
            min_size=(980, 660),
            text_select=True,
        )
        webview.start()
    except Exception:  # noqa: BLE001 - qualquer tropeço do backend gráfico
        return False
    return True


# Onde os navegadores costumam estar. O Edge existe em todo Windows 10.
_NAVEGADORES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def abrir_como_aplicativo(url: str, *, perfil: Path) -> bool:
    """Chrome/Edge em modo ``--app``: janela limpa, sem barra de endereço.

    O ``--user-data-dir`` não é capricho: sem um perfil próprio, o navegador já
    aberto assume a janela e o processo que criamos morre na hora — o programa
    acharia que a janela fechou e encerraria o servidor debaixo dela.
    """
    executavel = next((c for c in _NAVEGADORES if Path(c).exists()), None)
    if executavel is None:
        return False

    perfil.mkdir(parents=True, exist_ok=True)
    comando = [
        executavel,
        f"--app={url}",
        f"--user-data-dir={perfil}",
        f"--window-size={JANELA_LARGURA},{JANELA_ALTURA}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        # As três saídas explícitas são obrigatórias: num programa sem console
        # (que é como o ``.exe`` roda) não há handle padrão para herdar, e o
        # Popen morre com "WinError 6: identificador inválido" antes mesmo de
        # abrir o navegador.
        processo = subprocess.Popen(  # noqa: S603 - caminho fixo, sem shell
            comando,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    processo.wait()  # o programa vive enquanto a janela viver
    return True


def _avisar(mensagem: str) -> None:
    """Recado para quem clicou no ``.exe`` — ali não existe console para ler."""
    print(mensagem)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        with suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, mensagem, TITULO, 0x40)


def main() -> int:
    servidor = Servidor(porta_livre())
    if not servidor.subir():
        _avisar("Não consegui subir o serviço local de impressão.")
        return 1

    print(f"{TITULO} {VERSAO} — {servidor.url}")
    try:
        # Do melhor para o pior: janela nativa, janela de aplicativo do
        # navegador, e por último uma aba comum com um aviso.
        if abrir_janela(servidor.url):
            return 0
        if abrir_como_aplicativo(servidor.url, perfil=formatos.dir_dados() / "janela"):
            return 0
        webbrowser.open(servidor.url)
        _avisar(
            f"{TITULO} está rodando em {servidor.url}\n\n"
            "A tela abriu no seu navegador. Feche esta mensagem só quando terminar: "
            "o programa encerra junto."
        )
    except KeyboardInterrupt:
        pass
    finally:
        servidor.descer()
    return 0


def _saida_segura() -> None:
    """Num programa de janela não há ``stdout``, e ``None`` derruba bibliotecas.

    Qualquer código que faça ``sys.stdout.write`` ou ``.isatty()`` estoura. Um
    destino nulo custa nada e evita a classe inteira desses tropeços.
    """
    for nome in ("stdout", "stderr"):
        if getattr(sys, nome, None) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def executar() -> int:
    """Ponto de entrada do ``.exe``: nenhum erro pode morrer calado.

    Sem isto, qualquer exceção não tratada vira a caixa cinza "Unhandled
    exception in script" do empacotador, que não diz **o que** houve — e quem
    está no galpão não tem console para investigar.
    """
    _saida_segura()
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 - último anteparo antes do sistema
        import traceback

        _avisar(f"{TITULO} não conseguiu iniciar.\n\n{exc}\n\n{traceback.format_exc()[-900:]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(executar())

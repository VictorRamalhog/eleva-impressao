# Receita do arquivo **único** — o que vai para o Drive.
#
# Rodar:  uv run pyinstaller eleva-impressao-unico.spec --noconfirm
# Sai em: dist/Eleva Impressao 1.0.exe
#
# Por que existem duas receitas, e não uma:
#
# - `eleva-impressao.spec` produz uma **pasta**. Abre na hora (nada a
#   descompactar) e é o que se usa no dia a dia nesta máquina.
# - esta produz **um arquivo só**. Abre mais devagar, porque a cada execução
#   descompacta tudo numa pasta temporária — mas é a única forma decente de
#   mandar o programa por Drive ou WhatsApp para alguém que não é técnico:
#   baixar, clicar, pronto. Sem "extraia a pasta antes de executar", que é
#   exatamente onde as instalações caseiras costumam morrer.
#
# A tela de abertura existe por causa dessa espera: sem sinal nenhum na tela, a
# pessoa clica de novo achando que não abriu — e aí ficam dois programas
# disputando a mesma impressora.

from pathlib import Path

RAIZ = Path(SPECPATH)
PACOTE = RAIZ / "src" / "eleva_impressao"

a = Analysis(
    [str(PACOTE / "desktop.py")],
    pathex=[str(RAIZ / "src")],
    binaries=[],
    datas=[(str(PACOTE / "web"), "eleva_impressao_web")],
    hiddenimports=[
        "win32print",
        "win32ui",
        "win32con",
        "PIL.ImageWin",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "segno",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "pytest",
        "respx",
        "coverage",
        "apscheduler",
        "typer",
        "rich",
        "pypdf",
        "sqlite3",
        "matplotlib",
        "numpy",
        "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

splash = Splash(
    str(PACOTE / "web" / "abertura.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    always_on_top=False,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="Eleva Impressao 1.0",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX encolhe o executável, mas é justamente o que mais dispara falso
    # positivo em antivírus — e um aviso de vírus mata a instalação na casa de
    # alguém. Tamanho maior é o preço de não assustar.
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(PACOTE / "web" / "eleva.ico"),
)

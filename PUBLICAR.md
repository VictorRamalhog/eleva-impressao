# Publicar no GitHub

O que sua mãe baixa **não é o código**: é o `.exe` anexado a uma *Release*.
O repositório guarda o código; a Release guarda o programa pronto.

O `gh` (linha de comando do GitHub) **não está instalado nesta máquina**, então
os passos abaixo usam o site do GitHub para as partes que precisam dele.

---

## 1. Criar o repositório (só na primeira vez)

1. Abra <https://github.com/new>.
2. **Repository name:** `eleva-impressao`
3. **Public** (para ela baixar sem precisar de conta nem de login).
4. **Não** marque "Add a README file", "Add .gitignore" nem "Choose a license" —
   esses arquivos já existem aqui e criá-los lá faria conflito.
5. Clique em **Create repository**.

Copie a URL que aparece (algo como `https://github.com/SEU-USUARIO/eleva-impressao.git`)
e rode aqui na pasta do projeto:

```powershell
git remote add origin https://github.com/SEU-USUARIO/eleva-impressao.git
git push -u origin main
```

---

## 2. Publicar o programa (a Release)

O `.exe` **não fica no repositório** — ele é pesado (30 MB) e é gerado a partir
do código. Ele vai anexado à Release.

1. Gere o arquivo único, se ainda não existir:

   ```powershell
   uv run pyinstaller eleva-impressao-unico.spec
   ```

   Ele aparece em `dist\Eleva Impressao 1.0.exe`.

2. No GitHub, abra a aba **Releases** → **Create a new release**.
3. **Choose a tag** → digite `v1.0` → **Create new tag on publish**.
4. **Release title:** `Eleva Impressão 1.0`
5. Na descrição, escreva o que mudou. Na primeira vez basta:

   > Primeira versão. Baixe o arquivo `Eleva Impressao 1.0.exe` aqui embaixo,
   > salve na Área de Trabalho e dê dois cliques.

6. **Arraste os dois arquivos** para a caixa "Attach binaries":
   - `dist\Eleva Impressao 1.0.exe`
   - `LEIA-ME.txt`
7. Clique em **Publish release**.

Pronto. O botão **"Clique aqui para baixar"** do `README.md` aponta para
`releases/latest`, então ele passa a levar sozinho para a versão mais nova —
você nunca precisa mandar um link novo para ela.

---

## 3. Publicar uma versão nova depois

```powershell
uv run pytest -q                                 # tem de passar tudo
uv run ruff check src tests
uv run pyinstaller eleva-impressao-unico.spec    # gera o .exe novo

git add -A
git commit -m "descreva o que mudou"
git push
```

Depois repita o **passo 2** com uma tag nova (`v1.1`, `v1.2`, …) e anexe o
`.exe` recém-gerado. O link do README continua o mesmo.

> Antes de subir uma versão nova, lembre de mexer no número em
> `pyproject.toml` (`version`) e no nome do `.exe` dentro de
> `eleva-impressao-unico.spec`, para não publicar dois arquivos diferentes com
> o mesmo nome.

---

## O histórico do projeto antigo

Esta pasta já foi do **ERP do Mercado Livre**. Aquele histórico (27 commits) não
foi apagado: está guardado em `.git-erp-antigo/`, que o `.gitignore` mantém fora
do GitHub. Para consultá-lo:

```powershell
git --git-dir=.git-erp-antigo log --oneline
```

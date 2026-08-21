FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MATHGRAPH_DATA_DIR=/var/lib/mathweaver

WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        fonts-noto-cjk \
        latexmk \
        texlive-binaries \
        texlive-lang-chinese \
        texlive-latex-extra \
        texlive-xetex \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

RUN groupadd --system --gid 10001 mathweaver \
    && useradd --system --uid 10001 --gid mathweaver --no-create-home mathweaver \
    && mkdir -p /var/lib/mathweaver \
    && chown mathweaver:mathweaver /var/lib/mathweaver

COPY . .

# 运行用户只能读取应用代码；可变任务制品严格留在单独的数据卷目录。
RUN chown -R root:root /app \
    && chmod -R go-w /app \
    && chmod 0555 /app/scripts/docker_entrypoint.py

# 构建期真实编译最小中文 TeX，避免只检查可执行文件却遗漏字体/宏包。
RUN command -v latexmk \
    && command -v xelatex \
    && command -v synctex \
    && printf '%s\n' '\documentclass{ctexart}' '\begin{document}数学\end{document}' > /tmp/mathweaver-smoke.tex \
    && latexmk -xelatex -synctex=1 -interaction=nonstopmode -halt-on-error -outdir=/tmp /tmp/mathweaver-smoke.tex \
    && test -s /tmp/mathweaver-smoke.pdf \
    && test -s /tmp/mathweaver-smoke.synctex.gz \
    && rm -f /tmp/mathweaver-smoke.*

USER mathweaver
EXPOSE 5001
ENTRYPOINT ["python", "scripts/docker_entrypoint.py"]
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "300", "--bind", "0.0.0.0:5001", "api_v2:app"]


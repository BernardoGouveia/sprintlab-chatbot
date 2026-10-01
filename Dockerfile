# SprintLab TFC Chatbox — Hugging Face Docker Space
# Runs server.py (Groq-backed) on port 7860, the port HF Spaces routes to.

FROM python:3.11-slim

# HF Spaces run containers as a non-root user with uid 1000.
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

WORKDIR /home/user/app

# Only the runtime files — secrets come from Space Secrets at runtime.
# server.py é o entrypoint; o frontend fica ao lado dele (é servido a partir
# da pasta do server.py, não do diretório de trabalho).
COPY --chown=user server.py chatbox.html style.css app.js ./
# src/ tem todos os módulos (config, gitlab_api, analytics, report, charts,
# blame, code_commit, actions, read_tools, llm, cache, rate_limiter, ...).
COPY --chown=user src/ ./src/

EXPOSE 7860

CMD ["python", "server.py"]

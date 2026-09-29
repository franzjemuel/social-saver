FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY core core
COPY providers providers
COPY apps apps
COPY ops ops
RUN pip install --no-cache-dir .
CMD ["python","-m","apps.bot.main"]

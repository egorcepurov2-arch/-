FROM python:3.11-slim

LABEL maintainer="VUPSEN Squad <championship@nggti.ru>"
LABEL description="Autonomous corridor dispatch & supervisory control center"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Dependency specifications (Core runner operates purely on Python 3.11 stdlib for maximum speed and zero network reliance)
COPY requirements.txt /app/requirements.txt

# Copy source code and immutable reference contracts
COPY src/ /app/src/
COPY data/reference/ /app/data/reference/
COPY data/contract/ /app/data/contract/

# Entrypoint for streaming stdin/stdout processor
ENTRYPOINT ["python3", "-u", "-m", "src.app"]

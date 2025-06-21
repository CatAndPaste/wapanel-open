FROM green-base:latest
WORKDIR /app
COPY . ./admin
ENV PYTHONPATH=/app
CMD ["uvicorn", "admin.main:admin_app", "--host", "0.0.0.0", "--port", "8009", "--proxy-headers"]
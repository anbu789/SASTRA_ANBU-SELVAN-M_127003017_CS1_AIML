FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/opt/hf CUDA_VISIBLE_DEVICES=-1
WORKDIR /project/Code
COPY requirements.txt /tmp/requirements.txt
RUN pip install --retries 10 --timeout 120 --index-url https://download.pytorch.org/whl/cpu torch && pip install --retries 10 --timeout 120 -r /tmp/requirements.txt
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')" && chmod -R a+rX /opt/hf
ENV HF_HUB_OFFLINE=1

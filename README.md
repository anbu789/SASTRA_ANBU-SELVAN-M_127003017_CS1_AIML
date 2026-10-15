# AUTOSAR HLD Document Analysis Assistant: code

Code for Case Study 1. The notebook builds the data; the API and the web app serve it.

## Contents

| File | What it is |
| --- | --- |
| `Code.ipynb` | The pipeline: ingestion, indexing, extraction, retrieval and question answering, consistency checks, flow reports, Neo4j graph, evaluation |
| `qa_router.py` | Question router (answers structured questions from the database) and the citation check |
| `hld_core.py` | Retrieval, answers, review and export logic used by the API |
| `api.py` | FastAPI service with API-key roles (viewer, engineer, admin) |
| `app.py` | Streamlit web app |
| `generate_synthetic_hld.py` | Writes the synthetic HLD PDFs and the ground truth into `../Input_Data` |
| `Dockerfile`, `docker-compose.yml`, `requirements.txt` | Container packaging for the API and the web app |
| `store/` | SQLite database, ChromaDB index, chunk file and demo API keys, all created by the notebook |

## Requirements

- Linux with Docker and Docker Compose
- Ollama, with the `qwen2.5:3b` model
- About 16 GB of RAM. No GPU is needed, and none is used.
- An internet connection for the first container build and the model download

## Run the app (Docker)

```bash
# terminal 1: model server on the CPU, leave it open
CUDA_VISIBLE_DEVICES=-1 ollama serve
ollama pull qwen2.5:3b              # first time only

# terminal 2
cd Code
docker compose up -d --build        # first build takes several minutes
curl http://127.0.0.1:8000/health   # expect {"status":"ok","llm":"qwen2.5:3b","chunks":21}
```

Open `http://localhost:8501` and sign in with a key from `store/users.json`. Stop with `docker compose down`.

The compose file runs the containers as user 1000:1000 and mounts the project folder. If `id -u` and `id -g` print different numbers on your machine, change `user:` in `docker-compose.yml`.

## Run without Docker

```bash
pip install -r requirements.txt
CUDA_VISIBLE_DEVICES=-1 python -m uvicorn api:app --port 8000     # terminal 2
python -m streamlit run app.py                                     # terminal 3
```

## Rebuild the data from the notebook

Run `Code.ipynb` from the top, in order: setup, load inputs, ingestion, chunk/embed/index, extraction (needs Ollama), retrieval and question answering, consistency checks, flow reports, graph (needs Neo4j), the service files, and the evaluation cells.

- The extraction cell rewrites the extracted rows and removes any review decisions, so run it only if the documents change. After it, rebuild the findings, the flow reports and the graph.
- `generate_synthetic_hld.py` only needs to run if `Input_Data` is missing.

## Neo4j (optional, for the graph cells)

```bash
docker run -d --name neo4j-hld -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/YOUR_PASSWORD neo4j:5
```

The notebook asks for the password at run time and does not store it.

## Notes

- All models run locally on the CPU, and no document text leaves the machine.
- The test document is synthetic. All reported results come from it, so they will probably overstate performance on real HLDs.
- `store/users.json` holds demo API keys for a local server only.

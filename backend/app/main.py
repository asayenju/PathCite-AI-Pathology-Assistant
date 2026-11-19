from fastapi import FastAPI

app = FastAPI(title="PathCite API")


@app.get("/health")
def health():
    return {"status": "ok"}

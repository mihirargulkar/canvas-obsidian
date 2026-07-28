from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import extract, canvas, chat

app = FastAPI(title="Canvas Study Assistant")

@app.get("/api/graph")
def api_graph():
    return extract.graph_data()

@app.get("/api/concept/{name}")
def api_concept(name: str):
    d = extract.concept_data(name)
    return d if d else JSONResponse(status_code=404, content={"error": "not found"})

@app.get("/api/due")
def api_due(days: int = 7):
    try:
        rows = canvas.upcoming(days)
        return [{"due": d.isoformat(), "course": c, "name": n, "points": p}
                for d, c, n, p in rows]
    except Exception as e:
        return JSONResponse(content={"items": [], "warning": str(e)[:120]})

class Ask(BaseModel):
    q: str

@app.post("/api/ask")
def api_ask(a: Ask):
    return chat.answer(a.q)

app.mount("/", StaticFiles(directory="web", html=True), name="web")


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()

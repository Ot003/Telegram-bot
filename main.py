from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    print(data)
    return {"ok": True}

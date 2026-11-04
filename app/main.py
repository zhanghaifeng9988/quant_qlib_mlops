from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd
import mlflow.pyfunc
from fastapi import FastAPI, HTTPException, Query
import os

# 从环境变量读取模型别名，默认值为 "candidate"
MODEL_ALIAS = os.getenv("MODEL_ALIAS", "candidate")
MODEL_URI = f"models:/us-stock-lgbm-1d@{MODEL_ALIAS}"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_URI = f"sqlite:///{PROJECT_ROOT}/mlflow.db"
MODEL_URI = "models:/us-stock-lgbm-1d@candidate"
FEATURES_PATH = PROJECT_ROOT / "data" / "features" / "features_latest.parquet"

state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] Loading local features into memory (Pandas)...")
    # 用 Pandas 极速读取 Parquet 特征
    state["features_df"] = pd.read_parquet(FEATURES_PATH)
    print(f"[startup] Features loaded, shape: {state['features_df'].shape}")

    print(f"[startup] Loading model from Registry: {MODEL_URI}...")
    state["model"] = mlflow.pyfunc.load_model(MODEL_URI)
    
    print("[startup] Ready (lightweight mode).")
    yield
    print("[shutdown] Cleaning up...")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/predict")
def predict(date: str = Query(..., description="预测日期，格式 YYYY-MM-DD")):
    # features_df = state.get("features_df")
    model = state.get("model")
    if model is None:
        raise HTTPException(status_code=500, detail="Model not initialized")

    #if features_df is None or model is None:
        #raise HTTPException(status_code=500, detail="Model or Features not initialized")

    try:
        # 直接从 Pandas 内存中截取当天的特征
        target_date = pd.Timestamp(date)
        
         # 🔥 每次请求时，重新读取 Parquet 文件（Pandas 读 200MB 的 Parquet 只需几十毫秒，完全没压力）
        features_df = pd.read_parquet(FEATURES_PATH)

        # 我们的 Parquet 索引是 MultiIndex (datetime, instrument)
        if target_date not in features_df.index.get_level_values("datetime"):
             raise HTTPException(status_code=404, detail=f"No features available for {date}")

        # 截取单日数据
        df = features_df.xs(target_date, level="datetime").copy()
        
        # 预测
        preds = model.predict(df)
        
        # 整理结果并排序
        instruments = df.index.tolist()
        result = [{"instrument": inst, "score": float(score)} for inst, score in zip(instruments, preds)]
        result.sort(key=lambda x: x["score"], reverse=True)
        
        return {"date": date, "count": len(result), "predictions": result}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
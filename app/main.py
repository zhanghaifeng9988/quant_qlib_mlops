
# app/main.py
from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd
import mlflow.pyfunc
import qlib
from qlib.constant import REG_US
from qlib.contrib.data.handler import Alpha158
from fastapi import FastAPI, HTTPException, Query

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_URI = f"sqlite:///{PROJECT_ROOT}/mlflow.db"
MODEL_URI = "models:/us-stock-lgbm-1d@candidate"
QLIB_DATA_DIR = PROJECT_ROOT / "data" / "qlib_bin" / "us_data"

# 全局变量保存模型和 handler
state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时：初始化 Qlib 和加载模型
    print("[startup] Initializing Qlib...")
    qlib.init(provider_uri=str(QLIB_DATA_DIR), region=REG_US)
    
    print("[startup] Loading Alpha158 handler (this may take a few seconds)...")
    # 这里的 fit_end_time 与你训练时保持一致
    handler = Alpha158(
        start_time="2011-10-24",
        end_time="2026-10-16",
        fit_start_time="2011-10-24",
        fit_end_time="2023-12-31",
        instruments="all"
    )
    state["handler"] = handler

    print(f"[startup] Loading model from Registry: {MODEL_URI}...")
    state["model"] = mlflow.pyfunc.load_model(MODEL_URI)
    
    print("[startup] Ready.")
    yield
    print("[shutdown] Cleaning up...")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/predict")
def predict(date: str = Query(..., description="预测日期，格式 YYYY-MM-DD，例如 2023-01-03")):
    handler = state.get("handler")
    model = state.get("model")
    if not handler or not model:
        raise HTTPException(status_code=500, detail="Model or Handler not initialized")

    try:
        # 1. 提取当天的特征
        df = handler.fetch(selector=slice(date, date), col_set="feature", data_key="infer")
        
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data available for date {date}")
            
        # 2. 处理特征列名
        # 当只拉取单日数据时，列索引会塌缩成单层；如果是多日，则是两层
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(1)
        
        # 3. 模型预测
        # 这里直接传 DataFrame 给包装好的 pyfunc 模型
        preds = model.predict(df)
        
        # 4. 整理输出，返回按分数降序排列的股票列表
        if isinstance(df.index, pd.MultiIndex):
            instruments = df.index.get_level_values("instrument").tolist()
        else:
            # 如果只有一层索引，说明 Qlib 已经把它降级成了以股票代码为索引
            instruments = df.index.tolist()
        
        result = [
            {"instrument": inst, "score": float(score)}
            for inst, score in zip(instruments, preds)
        ]
        result.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "date": date,
            "count": len(result),
            "predictions": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

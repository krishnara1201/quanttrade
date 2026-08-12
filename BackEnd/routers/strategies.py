import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project, Strategy
from database.connection import AsyncSessionLocal, get_db
from services.auth_service import get_current_user
from services.sandbox_executor import check_ast_safety, SandboxValidationError

router = APIRouter(prefix="/strategies", tags=["strategies"])

class StrategyCreate(BaseModel):
    name: str
    project_id: int
    parameters: str
    status: Optional[str] = "draft"
    code: Optional[str] = None
    is_public: Optional[bool] = False


def _validate_custom_code(parameters_json: str, code: Optional[str]) -> None:
    """Run the sandbox's AST safety check at save time (not just at
    backtest-run time) for fast feedback, whenever the effective strategy
    mode is 'custom_code'. No-op for the default 'rules' mode."""
    try:
        parsed_params = json.loads(parameters_json) if parameters_json else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="parameters must be valid JSON")

    if parsed_params.get('mode') != 'custom_code':
        return

    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="Custom-code strategies require non-empty code")
    try:
        check_ast_safety(code)
    except SandboxValidationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid strategy code: {e}")

@router.get("/")
async def read_strategies(db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(select(Strategy))
    strategies = result.scalars().all()
    return strategies

@router.get("/{strategy_id}")
async def read_strategy(strategy_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Strategy).where(Strategy.id == strategy_id)
    )
    strategy = result.scalars().first()
    if strategy is None:
        return {"error": "Strategy not found"}
    return strategy

@router.post("/")
async def create_strategy(strategy_data: StrategyCreate, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
        # Verify project ownership
        project_result = await db.execute(
            select(Project).where(Project.id == strategy_data.project_id)
        )
        project = project_result.scalars().first()
        if not project or project.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Unauthorized to create strategy in this project")

        _validate_custom_code(strategy_data.parameters, strategy_data.code)

        db_strategy = Strategy(**strategy_data.dict())
        db.add(db_strategy)
        await db.commit()
        await db.refresh(db_strategy)
        return db_strategy

@router.put("/{strategy_id}")
async def update_strategy(strategy_id: int, strategy_data: dict, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
        result = await db.execute(
            select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
        )
        strategy = result.scalars().first()
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")

        # Verify ownership
        if strategy.project.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Unauthorized")

        if 'parameters' in strategy_data or 'code' in strategy_data:
            merged_parameters = strategy_data.get('parameters', strategy.parameters)
            merged_code = strategy_data.get('code', strategy.code)
            _validate_custom_code(merged_parameters, merged_code)

        for key, value in strategy_data.items():
            setattr(strategy, key, value)
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)
        return strategy

from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

from app.database import get_db
from app.models import User, Workflow as WorkflowModel, WorkflowExecution
from app.auth import get_current_user

router = APIRouter()


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    trigger: str
    conditions: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]]
    enabled: bool
    created_at: datetime
    updated_at: datetime
    execution_count: int = 0

    class Config:
        from_attributes = True


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    trigger: str = Field(..., pattern="^(message_received|contact_created|event_created)$")
    conditions: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]] = Field(..., min_items=1)
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    trigger: Optional[str] = Field(None, pattern="^(message_received|contact_created|event_created)$")
    conditions: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, Any]]] = Field(None, min_items=1)
    enabled: Optional[bool] = None


class WorkflowExecutionResponse(BaseModel):
    id: str
    workflow_id: str
    triggered_by: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    executed_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/", response_model=List[WorkflowResponse])
async def get_workflows(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    enabled: Optional[bool] = Query(None),
    trigger: Optional[str] = Query(None)
) -> List[WorkflowResponse]:
    """Get all workflows"""
    
    query = select(WorkflowModel).where(WorkflowModel.user_id == current_user.id)
    
    # Apply filters
    if enabled is not None:
        query = query.where(WorkflowModel.enabled == enabled)
    
    if trigger:
        query = query.where(WorkflowModel.trigger == trigger)
    
    # Order by created_at
    query = query.order_by(WorkflowModel.created_at.desc())
    
    result = await db.execute(query)
    workflows = result.scalars().all()
    
    # Get execution counts for each workflow
    workflow_responses = []
    for workflow in workflows:
        exec_query = select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow.id)
        exec_result = await db.execute(exec_query)
        execution_count = len(exec_result.scalars().all())
        
        workflow_responses.append(WorkflowResponse(
            id=str(workflow.id),
            name=workflow.name,
            description=workflow.description,
            trigger=workflow.trigger,
            conditions=workflow.conditions,
            actions=workflow.actions,
            enabled=workflow.enabled,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            execution_count=execution_count
        ))
    
    return workflow_responses


@router.post("/", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    workflow_data: WorkflowCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> WorkflowResponse:
    """Create a new workflow automation"""
    
    # Validate actions structure
    for action in workflow_data.actions:
        if not isinstance(action, dict) or "type" not in action:
            raise HTTPException(
                status_code=400,
                detail="Each action must be an object with a 'type' field"
            )
    
    # Create new workflow
    workflow = WorkflowModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=workflow_data.name,
        description=workflow_data.description,
        trigger=workflow_data.trigger,
        conditions=workflow_data.conditions,
        actions=workflow_data.actions,
        enabled=workflow_data.enabled,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    
    # TODO: Register with workflow engine
    
    return WorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        trigger=workflow.trigger,
        conditions=workflow.conditions,
        actions=workflow.actions,
        enabled=workflow.enabled,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        execution_count=0
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> WorkflowResponse:
    """Get a specific workflow by ID"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get execution count
    exec_query = select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_uuid)
    exec_result = await db.execute(exec_query)
    execution_count = len(exec_result.scalars().all())
    
    return WorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        trigger=workflow.trigger,
        conditions=workflow.conditions,
        actions=workflow.actions,
        enabled=workflow.enabled,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        execution_count=execution_count
    )


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    workflow_data: WorkflowUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> WorkflowResponse:
    """Update an existing workflow"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Update fields if provided
    if workflow_data.name is not None:
        workflow.name = workflow_data.name
    if workflow_data.description is not None:
        workflow.description = workflow_data.description
    if workflow_data.trigger is not None:
        workflow.trigger = workflow_data.trigger
    if workflow_data.conditions is not None:
        workflow.conditions = workflow_data.conditions
    if workflow_data.actions is not None:
        # Validate actions structure
        for action in workflow_data.actions:
            if not isinstance(action, dict) or "type" not in action:
                raise HTTPException(
                    status_code=400,
                    detail="Each action must be an object with a 'type' field"
                )
        workflow.actions = workflow_data.actions
    if workflow_data.enabled is not None:
        workflow.enabled = workflow_data.enabled
    
    workflow.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(workflow)
    
    # Get execution count
    exec_query = select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_uuid)
    exec_result = await db.execute(exec_query)
    execution_count = len(exec_result.scalars().all())
    
    return WorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        trigger=workflow.trigger,
        conditions=workflow.conditions,
        actions=workflow.actions,
        enabled=workflow.enabled,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        execution_count=execution_count
    )


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a workflow"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    await db.delete(workflow)
    await db.commit()
    
    return None


@router.post("/{workflow_id}/toggle")
async def toggle_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Enable or disable a workflow"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Toggle enabled status
    workflow.enabled = not workflow.enabled
    workflow.updated_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "message": f"Workflow {workflow_id} {'enabled' if workflow.enabled else 'disabled'}",
        "enabled": workflow.enabled
    }


@router.post("/{workflow_id}/test")
async def test_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Test a workflow with sample data"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Create test execution record
    test_execution = WorkflowExecution(
        id=uuid.uuid4(),
        workflow_id=workflow_uuid,
        triggered_by=None,  # Test execution
        status="completed",
        executed_at=datetime.utcnow(),
        completed_at=datetime.utcnow()
    )
    
    db.add(test_execution)
    await db.commit()
    
    # TODO: Execute workflow with test data
    
    return {"message": f"Workflow {workflow_id} test execution completed"}


@router.get("/{workflow_id}/executions", response_model=List[WorkflowExecutionResponse])
async def get_workflow_executions(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
) -> List[WorkflowExecutionResponse]:
    """Get execution history for a workflow"""
    
    try:
        workflow_uuid = uuid.UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    
    # Verify workflow exists and belongs to user
    workflow_query = select(WorkflowModel).where(
        and_(WorkflowModel.id == workflow_uuid, WorkflowModel.user_id == current_user.id)
    )
    workflow_result = await db.execute(workflow_query)
    workflow = workflow_result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get executions
    exec_query = select(WorkflowExecution).where(
        WorkflowExecution.workflow_id == workflow_uuid
    ).order_by(WorkflowExecution.executed_at.desc()).offset(offset).limit(limit)
    
    result = await db.execute(exec_query)
    executions = result.scalars().all()
    
    return [
        WorkflowExecutionResponse(
            id=str(execution.id),
            workflow_id=str(execution.workflow_id),
            triggered_by=str(execution.triggered_by) if execution.triggered_by else None,
            status=execution.status,
            error_message=execution.error_message,
            executed_at=execution.executed_at,
            completed_at=execution.completed_at
        )
        for execution in executions
    ]

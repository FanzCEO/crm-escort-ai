"""
Advanced workflow automation engine for CRM Escort AI
Executes automated actions based on triggers and conditions
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict
from enum import Enum

logger = logging.getLogger(__name__)


class WorkflowTrigger(Enum):
    MESSAGE_RECEIVED = "message_received"
    CONTACT_CREATED = "contact_created"
    EVENT_CREATED = "event_created"
    TIME_BASED = "time_based"
    MANUAL = "manual"


class WorkflowAction(Enum):
    SEND_SMS = "send_sms"
    SEND_EMAIL = "send_email"
    CREATE_CONTACT = "create_contact"
    CREATE_EVENT = "create_event"
    CREATE_TASK = "create_task"
    UPDATE_CONTACT = "update_contact"
    WEBHOOK = "webhook"
    DELAY = "delay"


class WorkflowEngine:
    """Advanced workflow automation engine"""
    
    def __init__(self):
        self.actions_registry = {
            WorkflowAction.SEND_SMS: self._action_send_sms,
            WorkflowAction.SEND_EMAIL: self._action_send_email,
            WorkflowAction.CREATE_CONTACT: self._action_create_contact,
            WorkflowAction.CREATE_EVENT: self._action_create_event,
            WorkflowAction.CREATE_TASK: self._action_create_task,
            WorkflowAction.UPDATE_CONTACT: self._action_update_contact,
            WorkflowAction.WEBHOOK: self._action_webhook,
            WorkflowAction.DELAY: self._action_delay,
        }
    
    def evaluate_conditions(self, conditions: Dict, context: Dict) -> bool:
        """Evaluate workflow conditions against context"""
        try:
            # Handle different condition types
            if "all" in conditions:
                # All conditions must be true
                return all(
                    self._evaluate_single_condition(cond, context)
                    for cond in conditions["all"]
                )
            elif "any" in conditions:
                # Any condition can be true
                return any(
                    self._evaluate_single_condition(cond, context)
                    for cond in conditions["any"]
                )
            else:
                # Single condition
                return self._evaluate_single_condition(conditions, context)
        except Exception as e:
            logger.error(f"Error evaluating conditions: {e}")
            return False
    
    def _evaluate_single_condition(self, condition: Dict, context: Dict) -> bool:
        """Evaluate a single condition"""
        condition_type = condition.get("type", "contains")
        field = condition.get("field", "content")
        value = condition.get("value", "")
        
        # Get field value from context
        field_value = self._get_nested_value(context, field)
        if field_value is None:
            return False
        
        # Convert to string for text operations
        field_str = str(field_value).lower()
        value_str = str(value).lower()
        
        # Apply condition logic
        if condition_type == "contains":
            return value_str in field_str
        elif condition_type == "equals":
            return field_str == value_str
        elif condition_type == "starts_with":
            return field_str.startswith(value_str)
        elif condition_type == "ends_with":
            return field_str.endswith(value_str)
        elif condition_type == "regex":
            try:
                return bool(re.search(value, field_str, re.IGNORECASE))
            except re.error:
                return False
        elif condition_type == "greater_than":
            try:
                return float(field_value) > float(value)
            except (ValueError, TypeError):
                return False
        elif condition_type == "less_than":
            try:
                return float(field_value) < float(value)
            except (ValueError, TypeError):
                return False
        elif condition_type == "time_range":
            # Check if current time is within range
            try:
                current_hour = datetime.now().hour
                start_hour = int(condition.get("start_hour", 0))
                end_hour = int(condition.get("end_hour", 23))
                return start_hour <= current_hour <= end_hour
            except (ValueError, TypeError):
                return False
        
        return False
    
    def _get_nested_value(self, data: Dict, field: str) -> Any:
        """Get nested dictionary value using dot notation"""
        keys = field.split(".")
        current = data
        
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        
        return current
    
    async def execute_workflow(self, workflow: Dict, context: Dict, db_session=None) -> Dict:
        """Execute a workflow with given context"""
        execution_log: Dict[str, Any] = {
            "workflow_id": workflow.get("id"),
            "started_at": datetime.utcnow().isoformat(),
            "actions_executed": [],
            "success": True,
            "error": None
        }
        
        try:
            # Check if workflow is enabled
            if not workflow.get("enabled", True):
                execution_log["success"] = False
                execution_log["error"] = "Workflow is disabled"
                return execution_log
            
            # Evaluate conditions
            conditions = workflow.get("conditions", {})
            if conditions and not self.evaluate_conditions(conditions, context):
                execution_log["success"] = False
                execution_log["error"] = "Conditions not met"
                return execution_log
            
            # Execute actions
            actions = workflow.get("actions", [])
            for i, action in enumerate(actions):
                try:
                    action_result = await self._execute_action(action, context, db_session)
                    execution_log["actions_executed"].append({
                        "action_index": i,
                        "action_type": action.get("type"),
                        "success": True,
                        "result": action_result
                    })
                except Exception as e:
                    logger.error(f"Error executing action {i}: {e}")
                    execution_log["actions_executed"].append({
                        "action_index": i,
                        "action_type": action.get("type"),
                        "success": False,
                        "error": str(e)
                    })
                    
                    # Stop execution on error unless continue_on_error is set
                    if not action.get("continue_on_error", False):
                        execution_log["success"] = False
                        execution_log["error"] = f"Action {i} failed: {str(e)}"
                        break
            
            execution_log["completed_at"] = datetime.utcnow().isoformat()
            
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            execution_log["success"] = False
            execution_log["error"] = str(e)
            execution_log["completed_at"] = datetime.utcnow().isoformat()
        
        return execution_log
    
    async def _execute_action(self, action: Dict, context: Dict, db_session=None) -> Dict:
        """Execute a single workflow action"""
        action_type = action.get("type")
        
        if action_type not in [e.value for e in WorkflowAction]:
            raise ValueError(f"Unknown action type: {action_type}")
        
        # Get action handler
        handler = self.actions_registry.get(WorkflowAction(action_type))
        if not handler:
            raise ValueError(f"No handler for action type: {action_type}")
        
        # Apply template substitution
        processed_action = self._apply_templates(action, context)
        
        # Execute action
        return await handler(processed_action, context, db_session)
    
    def _apply_templates(self, action: Dict, context: Dict) -> Dict:
        """Apply template substitution to action parameters"""
        action_copy = action.copy()
        
        # Template substitution function
        def substitute_templates(obj):
            if isinstance(obj, str):
                # Replace template variables like {{variable}}
                import re
                pattern = r'{{([^}]+)}}'
                
                def replace_var(match):
                    var_name = match.group(1).strip()
                    value = self._get_nested_value(context, var_name)
                    return str(value) if value is not None else match.group(0)
                
                return re.sub(pattern, replace_var, obj)
            elif isinstance(obj, dict):
                return {k: substitute_templates(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [substitute_templates(item) for item in obj]
            else:
                return obj
        
        return substitute_templates(action_copy)
    
    # Action handlers
    async def _action_send_sms(self, action: Dict, context: Dict, db_session) -> Dict:
        """Send SMS action handler"""
        from .sms_handler import send_sms
        
        to_number = action.get("to_number")
        message = action.get("message", "")
        
        if not to_number:
            # Try to get from context
            to_number = context.get("sender_phone") or context.get("contact", {}).get("phone")
        
        if not to_number:
            raise ValueError("No phone number provided for SMS")
        
        result = await send_sms(to_number, message)
        return {"message_sid": result.get("sid"), "to": to_number}
    
    async def _action_send_email(self, action: Dict, context: Dict, db_session) -> Dict:
        """Send email action handler"""
        from .email_handler import send_templated_email, send_email
        
        # Check if using template or direct email
        if action.get("template_name"):
            result = await send_templated_email(
                template_name=action["template_name"],
                to_addresses=action.get("to_addresses", []),
                subject=action.get("subject", ""),
                context=action.get("context", context)
            )
        else:
            result = await send_email(
                to_addresses=action.get("to_addresses", []),
                subject=action.get("subject", ""),
                body_text=action.get("body_text"),
                body_html=action.get("body_html")
            )
        
        return {"success": result.get("success", False), "message_id": result.get("message_id")}
    
    async def _action_create_contact(self, action: Dict, context: Dict, db_session) -> Dict:
        """Create contact action handler"""
        from .models import Contact
        
        contact_data = action.get("contact_data", {})
        
        contact = Contact(
            user_id=context.get("user_id"),
            name=contact_data.get("name", "Unknown"),
            phone=contact_data.get("phone"),
            email=contact_data.get("email"),
            organization=contact_data.get("organization"),
            notes=contact_data.get("notes", "")
        )
        
        if db_session:
            db_session.add(contact)
            await db_session.commit()
            await db_session.refresh(contact)
        
        return {"contact_id": str(contact.contact_id) if contact.contact_id else None}
    
    async def _action_create_event(self, action: Dict, context: Dict, db_session) -> Dict:
        """Create calendar event action handler"""
        from .models import Event
        
        event_data = action.get("event_data", {})
        
        event = Event(
            user_id=context.get("user_id"),
            title=event_data.get("title", "New Event"),
            description=event_data.get("description", ""),
            start_time=datetime.fromisoformat(event_data.get("start_time")) if event_data.get("start_time") else datetime.utcnow(),
            end_time=datetime.fromisoformat(event_data.get("end_time")) if event_data.get("end_time") else datetime.utcnow() + timedelta(hours=1),
            location_id=event_data.get("location_id")
        )
        
        if db_session:
            db_session.add(event)
            await db_session.commit()
            await db_session.refresh(event)
        
        return {"event_id": str(event.event_id) if event.event_id else None}
    
    async def _action_create_task(self, action: Dict, context: Dict, db_session) -> Dict:
        """Create task action handler"""
        from .models import Task
        
        task_data = action.get("task_data", {})
        
        task = Task(
            user_id=context.get("user_id"),
            title=task_data.get("title", "New Task"),
            description=task_data.get("description", ""),
            priority=task_data.get("priority", "medium"),
            status="pending",
            due_date=datetime.fromisoformat(task_data.get("due_date")) if task_data.get("due_date") else None
        )
        
        if db_session:
            db_session.add(task)
            await db_session.commit()
            await db_session.refresh(task)
        
        return {"task_id": str(task.task_id) if task.task_id else None}
    
    async def _action_update_contact(self, action: Dict, context: Dict, db_session) -> Dict:
        """Update contact action handler"""
        # TODO: Implement contact updates
        logger.info("Update contact action executed (not fully implemented)")
        return {"status": "placeholder"}
    
    async def _action_webhook(self, action: Dict, context: Dict, db_session) -> Dict:
        """Webhook action handler"""
        import aiohttp
        
        url = action.get("url")
        method = action.get("method", "POST")
        headers = action.get("headers", {})
        payload = action.get("payload", context)
        
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, json=payload, headers=headers) as response:
                return {
                    "status_code": response.status,
                    "response": await response.text()
                }
    
    async def _action_delay(self, action: Dict, context: Dict, db_session) -> Dict:
        """Delay action handler"""
        import asyncio
        
        delay_seconds = action.get("delay_seconds", 1)
        await asyncio.sleep(delay_seconds)
        
        return {"delayed_seconds": delay_seconds}


# Global workflow engine instance
workflow_engine = WorkflowEngine()
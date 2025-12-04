"""
Email handling and automation for CRM Escort AI
Supports SMTP, email templates, and automated campaigns
"""
import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass
from datetime import datetime
import os
import jinja2

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    smtp_server: str
    smtp_port: int
    username: str
    password: str
    use_tls: bool = True
    use_ssl: bool = False
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None


@dataclass
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class EmailMessage:
    to_addresses: List[str]
    subject: str
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    cc_addresses: Optional[List[str]] = None
    bcc_addresses: Optional[List[str]] = None
    attachments: Optional[List[EmailAttachment]] = None
    reply_to: Optional[str] = None
    priority: str = "normal"  # low, normal, high
    
    def __post_init__(self):
        if self.cc_addresses is None:
            self.cc_addresses = []
        if self.bcc_addresses is None:
            self.bcc_addresses = []
        if self.attachments is None:
            self.attachments = []


class EmailTemplateManager:
    """Email template management with Jinja2"""
    
    def __init__(self, templates_dir: str = "templates/email"):
        self.templates_dir = templates_dir
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(templates_dir),
            autoescape=jinja2.select_autoescape(['html', 'xml'])
        )
    
    def render_template(self, template_name: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Render email template with context variables"""
        try:
            # Load HTML template
            html_template = self.jinja_env.get_template(f"{template_name}.html")
            html_content = html_template.render(context)
            
            # Try to load text template
            text_content = None
            try:
                text_template = self.jinja_env.get_template(f"{template_name}.txt")
                text_content = text_template.render(context)
            except jinja2.TemplateNotFound:
                # Generate simple text from HTML if no text template
                import re
                text_content = re.sub(r'<[^>]+>', '', html_content)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
            
            return {
                "html": html_content,
                "text": text_content
            }
            
        except jinja2.TemplateNotFound:
            raise ValueError(f"Email template '{template_name}' not found")
        except Exception as e:
            logger.error(f"Error rendering email template: {e}")
            raise


class EmailHandler:
    """Advanced email handling with SMTP support"""
    
    def __init__(self, config: EmailConfig):
        self.config = config
        self.template_manager = EmailTemplateManager()
    
    async def send_email(self, message: EmailMessage) -> Dict[str, Any]:
        """Send email message via SMTP"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = self._format_sender()
            msg['To'] = ', '.join(message.to_addresses)
            msg['Subject'] = message.subject
            
            if message.cc_addresses:
                msg['Cc'] = ', '.join(message.cc_addresses)
            
            if message.reply_to:
                msg['Reply-To'] = message.reply_to
            
            # Set priority
            if message.priority == "high":
                msg['X-Priority'] = '1'
                msg['X-MSMail-Priority'] = 'High'
            elif message.priority == "low":
                msg['X-Priority'] = '5'
                msg['X-MSMail-Priority'] = 'Low'
            
            # Add text part
            if message.body_text:
                text_part = MIMEText(message.body_text, 'plain', 'utf-8')
                msg.attach(text_part)
            
            # Add HTML part
            if message.body_html:
                html_part = MIMEText(message.body_html, 'html', 'utf-8')
                msg.attach(html_part)
            
            # Add attachments
            for attachment in (message.attachments or []):
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.content)
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename= {attachment.filename}'
                )
                msg.attach(part)
            
            # Send email
            all_recipients = (
                message.to_addresses +
                (message.cc_addresses or []) +
                (message.bcc_addresses or [])
            )
            
            if self.config.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.config.smtp_server, self.config.smtp_port, context=context) as server:
                    if self.config.username and self.config.password:
                        server.login(self.config.username, self.config.password)
                    server.sendmail(self.config.sender_email or self.config.username, all_recipients, msg.as_string())
            else:
                with smtplib.SMTP(self.config.smtp_server, self.config.smtp_port) as server:
                    if self.config.use_tls:
                        server.starttls()
                    if self.config.username and self.config.password:
                        server.login(self.config.username, self.config.password)
                    server.sendmail(self.config.sender_email or self.config.username, all_recipients, msg.as_string())
            
            logger.info(f"Email sent successfully to {len(all_recipients)} recipients")
            return {
                "success": True,
                "message_id": msg.get('Message-ID'),
                "recipients": len(all_recipients),
                "sent_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return {
                "success": False,
                "error": str(e),
                "failed_at": datetime.utcnow().isoformat()
            }
    
    async def send_templated_email(
        self, 
        template_name: str, 
        to_addresses: List[str],
        subject: str,
        context: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """Send email using template"""
        try:
            rendered_content = self.template_manager.render_template(template_name, context)
            
            message = EmailMessage(
                to_addresses=to_addresses,
                subject=subject,
                body_text=rendered_content["text"],
                body_html=rendered_content["html"],
                **kwargs
            )
            
            return await self.send_email(message)
            
        except Exception as e:
            logger.error(f"Error sending templated email: {e}")
            return {
                "success": False,
                "error": str(e),
                "failed_at": datetime.utcnow().isoformat()
            }
    
    def _format_sender(self) -> str:
        """Format sender email with optional name"""
        if self.config.sender_name:
            return f"{self.config.sender_name} <{self.config.sender_email or self.config.username}>"
        else:
            return self.config.sender_email or self.config.username


class EmailCampaignManager:
    """Email campaign automation and management"""
    
    def __init__(self, email_handler: EmailHandler):
        self.email_handler = email_handler
    
    async def send_campaign(
        self,
        campaign_name: str,
        template_name: str,
        recipients: List[Dict[str, Any]],
        subject_template: str,
        global_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send email campaign to multiple recipients"""
        if global_context is None:
            global_context = {}
        
        results: Dict[str, Any] = {
            "campaign_name": campaign_name,
            "total_recipients": len(recipients),
            "successful_sends": 0,
            "failed_sends": 0,
            "results": [],
            "started_at": datetime.utcnow().isoformat()
        }
        
        for recipient in recipients:
            try:
                # Merge recipient context with global context
                context = {**global_context, **recipient}
                
                # Render subject with context
                subject = self._render_string_template(subject_template, context)
                
                # Send email
                result = await self.email_handler.send_templated_email(
                    template_name=template_name,
                    to_addresses=[recipient["email"]],
                    subject=subject,
                    context=context
                )
                
                if result["success"]:
                    results["successful_sends"] += 1
                else:
                    results["failed_sends"] += 1
                
                results["results"].append({
                    "email": recipient["email"],
                    "success": result["success"],
                    "error": result.get("error")
                })
                
            except Exception as e:
                logger.error(f"Error sending campaign email to {recipient.get('email', 'unknown')}: {e}")
                results["failed_sends"] += 1
                results["results"].append({
                    "email": recipient.get("email", "unknown"),
                    "success": False,
                    "error": str(e)
                })
        
        results["completed_at"] = datetime.utcnow().isoformat()
        logger.info(f"Campaign '{campaign_name}' completed: {results['successful_sends']}/{results['total_recipients']} successful")
        
        return results
    
    def _render_string_template(self, template_string: str, context: Dict[str, Any]) -> str:
        """Render a string template with context"""
        template = jinja2.Template(template_string)
        return template.render(context)


# Email configuration from environment variables
def get_email_config() -> EmailConfig:
    """Get email configuration from environment variables"""
    return EmailConfig(
        smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        username=os.getenv("SMTP_USERNAME", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        use_ssl=os.getenv("SMTP_USE_SSL", "false").lower() == "true",
        sender_name=os.getenv("SENDER_NAME"),
        sender_email=os.getenv("SENDER_EMAIL")
    )


# Global email handler instance
email_handler = None

def get_email_handler() -> EmailHandler:
    """Get global email handler instance"""
    global email_handler
    if email_handler is None:
        config = get_email_config()
        email_handler = EmailHandler(config)
    return email_handler


async def send_email(
    to_addresses: List[str],
    subject: str,
    body_text: Optional[str] = None,
    body_html: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Convenience function to send email"""
    handler = get_email_handler()
    message = EmailMessage(
        to_addresses=to_addresses,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        **kwargs
    )
    return await handler.send_email(message)


async def send_templated_email(
    template_name: str,
    to_addresses: List[str],
    subject: str,
    context: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """Convenience function to send templated email"""
    handler = get_email_handler()
    return await handler.send_templated_email(
        template_name, to_addresses, subject, context, **kwargs
    )
from app.db.session import Base

# All models should be imported here for Alembic autogenerate
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question, AnalysisTask
from app.models.ai_question import AIGeneratedQuestion, AIQuestionAnswer
from app.models.conversation import Conversation, ConversationMessage
from app.models.knowledge_state import UserKnowledgeState
from app.models.personality import AgentPersonality
from app.models.composition import CompositionCorrection
from app.models.oral_assessment import ListeningTest, DictationTask, MandarinTestRecord, OralRecord
from app.models.llm_usage import LlmUsageLog
from app.models.favorite import UserFavorite

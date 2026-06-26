from app.db.session import Base

# All models should be imported here for Alembic autogenerate
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question, AnalysisTask
from app.models.ai_question import AIGeneratedQuestion, AIQuestionAnswer

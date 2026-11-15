"""Real production export routes/repository, isolated disposable synthetic database.

Not a full authenticated desktop acceptance: the minimal host intentionally mounts
only export routes. The existing backend suite covers authentication middleware.
"""
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uvicorn

from offerpilot.models import Base
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository
from offerpilot.resume_export_routes import register_resume_export_routes


def main():
    with tempfile.TemporaryDirectory(prefix='resume-export-browser-') as directory:
        engine = create_engine(f'sqlite:///{Path(directory) / "fixture.db"}',
                               connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        resumes = ResumesRepository(sessionmaker(bind=engine))
        for count in (1, 100):
            resumes.create(ResumeCreate(title='中文简历', source='manual', content_json={
                'contact': {'name': '张小明', 'email': 'demo@example.test'},
                'skills': ['Python', '中文排版'],
                'additional_text': '\n'.join(f'第 {i + 1} 条：实际中文分页测试，保存后的简历事实。' for i in range(count)),
            }))
        app = FastAPI()
        register_resume_export_routes(app, resumes)
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')


if __name__ == '__main__':
    main()

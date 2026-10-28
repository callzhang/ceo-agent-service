"""Narrow Settings adapters for repository Skill discovery and installation."""
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr

from app.skill_repositories import SkillRepositoryError


class SkillSearchRequest(BaseModel):
    query: StrictStr


class SkillInstallRequest(BaseModel):
    repo_url: StrictStr
    commit: StrictStr
    skill_path: StrictStr


def _failure(error):
    if isinstance(error, TimeoutError):
        status, code = 504, 'timeout'
    elif isinstance(error, SkillRepositoryError):
        status, code = 502, 'upstream_error'
    elif 'conflict' in str(error):
        status, code = 409, 'conflict'
    else:
        status, code = 422, 'validation_error'
    return JSONResponse({'ok': False, 'code': code, 'message': str(error), 'details': {}}, status_code=status)


def register_skill_installation_routes(app: FastAPI, *, discovery_factory, installation_factory) -> None:
    @app.post('/api/console/settings/skill-search')
    def search(payload: SkillSearchRequest):
        try:
            return asdict(discovery_factory().search(payload.query))
        except (ValueError, SkillRepositoryError, TimeoutError) as exc:
            return _failure(exc)

    @app.post('/api/console/settings/skill-installs')
    def install(payload: SkillInstallRequest):
        try:
            result = installation_factory().install(**payload.model_dump())
        except (ValueError, SkillRepositoryError, TimeoutError) as exc:
            return _failure(exc)
        return JSONResponse(asdict(result), status_code=201 if result.install_status == 'installed' else 200)

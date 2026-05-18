from setuptools import find_packages, setup


setup(
    name="ceo-agent-local-service",
    version="0.1.0",
    packages=find_packages(include=["ceo_agent_service*"]),
    python_requires=">=3.11",
    install_requires=[
        "pydantic>=2.10",
    ],
    extras_require={
        "dev": [
            "pytest>=8.3",
        ],
    },
)

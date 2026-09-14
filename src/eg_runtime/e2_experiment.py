"""LLM-generated proposal experiment using the existing E1 runner."""

from __future__ import annotations

import argparse
from pathlib import Path

from eg_runtime.e1_experiment import (
    E1Configuration,
    run_e1_experiment,
)
from eg_runtime.ollama_adapter import (
    OllamaProposalGenerator,
    OllamaProposalGeneratorConfig,
)


DEFAULT_SEEDS = (101, 202, 303)


def load_configuration(path: str | Path) -> E1Configuration:
    """Load an E1-compatible experiment configuration."""

    return E1Configuration.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_ollama_generator_factory(
    *,
    model: str,
    host: str,
    temperature: float,
    timeout_seconds: float,
    seeds: tuple[int, ...],
):
    """Return one Ollama proposal generator per repetition."""

    def factory(repetition_index: int) -> OllamaProposalGenerator:
        if repetition_index >= len(seeds):
            raise ValueError(
                "Not enough seeds for configured repetitions: "
                f"index={repetition_index}, seeds={len(seeds)}"
            )

        return OllamaProposalGenerator(
            OllamaProposalGeneratorConfig(
                model=model,
                host=host,
                temperature=temperature,
                seed=seeds[repetition_index],
                timeout_seconds=timeout_seconds,
            )
        )

    return factory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run E2 with LLM-generated plans and proposals."
    )
    parser.add_argument(
        "--config",
        required=True,
    )
    parser.add_argument(
        "--output",
        required=True,
    )
    parser.add_argument(
        "--base-directory",
        default=".",
    )
    parser.add_argument(
        "--model",
        default="qwen3.5:9b-q4_K_M",
    )
    parser.add_argument(
        "--host",
        default="http://localhost:11434",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=list(DEFAULT_SEEDS),
    )

    args = parser.parse_args()

    configuration = load_configuration(args.config)

    if configuration.repetitions > len(args.seeds):
        raise ValueError(
            f"Configuration requires {configuration.repetitions} "
            f"repetitions but only {len(args.seeds)} seeds were supplied."
        )

    generator_factory = build_ollama_generator_factory(
        model=args.model,
        host=args.host,
        temperature=args.temperature,
        timeout_seconds=args.timeout,
        seeds=tuple(args.seeds),
    )

    artifacts = run_e1_experiment(
        configuration,
        base_directory=args.base_directory,
        output_directory=args.output,
        proposal_generator_factory=generator_factory,
        continue_on_generation_failure=True,
    )

    print(f"E2 completed: {artifacts}")


if __name__ == "__main__":
    main()

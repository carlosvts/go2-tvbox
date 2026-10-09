"""Interpretador: texto livre do STT entra, sai o ID de um comando ou nada.

Usado no modo `STT_MODE=livre`, em que o Vosk transcreve sem gramática e a
pessoa pode falar com variações ("por favor anda pra frente agora"). Os
vocabulários e limiares ficam em `config/interpreter.json`.

A ordem de avaliação é fixa:

1. Movimento: verbo + direção ("anda para frente" → `andar_frente`). Vem
   primeiro para o "para" da frase nunca ser lido como parada.
2. Parada: frase curta com uma palavra de parada.
3. Ação: fuzzy da frase inteira contra as frases de referência de cada ação.

Na dúvida não sai comando: negação, direção sem verbo e mais de um verbo,
direção ou ação na mesma frase dão `None`. Só a parada escapa da negação.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz

from sense.commands import INTERPRETER_FILE, normalize

log = logging.getLogger(__name__)

Vocabulary = dict[str, tuple[str, ...]]  # nome → variações normalizadas


@dataclass(frozen=True)
class Interpretation:
    id: Optional[str]  # None = nenhum comando
    score: float  # nota de 0 a 100 do que decidiu
    reason: str  # por que saiu este resultado, para o log
    is_stop: bool = False


def _vocabulary(entries: dict[str, list[str]]) -> Vocabulary:
    return {name: tuple(normalize(v) for v in variants) for name, variants in entries.items()}


class Interpreter:
    def __init__(self, data: dict) -> None:
        thresholds = data["limiares"]
        self._slot_threshold: float = thresholds["slot"]
        self._action_threshold: float = thresholds["acao"]
        self._stop_threshold: float = thresholds["parada"]
        self._fuzzy_min_len: int = data["fuzzy_min_letras"]
        self._negation = {normalize(word) for word in data["negacao"]}
        self._prepositions = {normalize(word) for word in data["preposicoes"]}
        self._verbs = _vocabulary(data["verbos"])
        self._directions = _vocabulary(data["direcoes"])
        self._actions = _vocabulary(data["acoes"])
        self._stop_id: str = data["parada"]["id"]
        self._stop_words = tuple(normalize(word) for word in data["parada"]["palavras"])
        self._stop_max_words: int = data["parada"]["max_palavras"]
        # Palavra que já é de algum vocabulário não entra em fuzzy com outra:
        # "deita" está a 83 de "direita" e viraria direção.
        vocabularies = (self._verbs, self._directions, self._actions)
        self._known = {
            word
            for vocabulary in vocabularies
            for variants in vocabulary.values()
            for variant in variants
            for word in variant.split()
        } | set(self._stop_words)

    @classmethod
    def load(cls, path: Path = INTERPRETER_FILE) -> "Interpreter":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def interpret(self, text: str) -> Interpretation:
        """Decide o comando de `text`. Todo resultado traz o motivo."""
        result = self._decide(normalize(text).split())
        log.debug(
            "Interpretador: %r → %s (nota %.0f; %s).", text, result.id, result.score, result.reason
        )
        return result

    def _decide(self, words: list[str]) -> Interpretation:
        if not words:
            return Interpretation(None, 0.0, "texto vazio")
        negated = any(word in self._negation for word in words)

        content = [word for word in words if word not in self._prepositions]
        verbs = self._slots(content, self._verbs)
        directions = self._slots(content, self._directions)
        if len(verbs) > 1 or len(directions) > 1:
            found = sorted(verbs) + sorted(directions)
            log.info("Interpretador: frase com mais de um verbo ou direção (%s).", ", ".join(found))
            return Interpretation(None, 0.0, "mais de um verbo ou direção: " + ", ".join(found))
        if verbs and directions:
            (verb, verb_score), (direction, direction_score) = *verbs.items(), *directions.items()
            score = min(verb_score, direction_score)
            if negated:
                return Interpretation(None, score, "negação")
            return Interpretation(f"{verb}_{direction}", score, "verbo + direção")
        if directions:
            return Interpretation(None, 0.0, f"direção {next(iter(directions))!r} sem verbo")

        if len(set(words)) <= self._stop_max_words:
            stop_score = max(self._similarity(w, s) for w in words for s in self._stop_words)
            if stop_score >= self._stop_threshold:
                return Interpretation(self._stop_id, stop_score, "palavra de parada", is_stop=True)

        if negated:
            return Interpretation(None, 0.0, "negação")
        return self._action(words)

    def _similarity(self, word: str, candidate: str) -> float:
        """Nota de 0 a 100. Palavra curta ou já conhecida só vale se for idêntica."""
        if word == candidate:
            return 100.0
        if word in self._known or min(len(word), len(candidate)) < self._fuzzy_min_len:
            return 0.0
        return fuzz.ratio(word, candidate)

    def _slots(self, words: list[str], vocabulary: Vocabulary) -> dict[str, float]:
        """Nomes do vocabulário presentes na frase, cada um com a melhor nota."""
        found: dict[str, float] = {}
        for name, variants in vocabulary.items():
            scores = [self._similarity(word, variant) for word in words for variant in variants]
            score = max(scores, default=0.0)
            if score >= self._slot_threshold:
                found[name] = score
        return found

    def _action(self, words: list[str]) -> Interpretation:
        text = " ".join(words)
        found: dict[str, float] = {}
        for name, references in self._actions.items():
            for reference in references:
                score = fuzz.token_set_ratio(text, reference)
                # O token_set_ratio dá 100 se a frase tiver só uma das palavras
                # da referência ("motores" × "desligar motores"); por isso
                # todas as palavras dela têm de aparecer.
                complete = all(
                    any(self._similarity(word, r) >= self._slot_threshold for word in words)
                    for r in reference.split()
                )
                if complete and score >= self._action_threshold:
                    found[name] = max(score, found.get(name, 0.0))
        if len(found) > 1:
            log.info("Interpretador: frase com mais de uma ação (%s).", ", ".join(sorted(found)))
            return Interpretation(None, 0.0, "mais de uma ação: " + ", ".join(sorted(found)))
        if found:
            ((name, score),) = found.items()
            return Interpretation(name, score, "ação")
        return Interpretation(None, 0.0, "nada reconhecido")


_default: Optional[Interpreter] = None


def interpretar(texto: str) -> Optional[str]:
    """ID do comando de `texto` segundo `config/interpreter.json`, ou None."""
    global _default
    if _default is None:
        _default = Interpreter.load()
    return _default.interpret(texto).id

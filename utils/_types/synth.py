"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from typing import Any, TypedDict


__all__ = ('SpeakersResponse', 'KanaResponse')


class _IDAndName(TypedDict):
    id: int
    name: str


class SpeakersResponse(TypedDict):
    name: str
    speaker_uuid: str
    styles: list[_IDAndName]
    version: str


class _MorasResponse(TypedDict):
    text: str
    consonant: Any | None
    consonant_length: Any | None
    vowel: str
    vowel_length: float
    pitch: float


class AccentResponse(TypedDict):
    moras: list[_MorasResponse]
    accent: int
    pause_mora: Any | None
    is_interrogative: bool


class KanaResponse(TypedDict):
    accent_phrases: list[AccentResponse]
    speedScale: float
    pitchScale: float
    intonationScale: float
    volumeScale: float
    prePhonemeLength: float
    postPhonemeLength: float
    outputSamplingRate: int
    outputStereo: bool
    kana: str

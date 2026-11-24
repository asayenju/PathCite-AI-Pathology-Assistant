from fastapi import APIRouter, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.images import read_upload
from app.transcripts import service
from app.transcripts.schemas import TranscriptImport

router = APIRouter()


@router.post(
    "/transcripts/import",
    response_model=TranscriptImport,
    summary="Import a diarized speech-to-text transcript",
    description=(
        "Upload one JSON transcript from Deepgram, AWS Transcribe (Medical) or diarized Whisper "
        "(WhisperX, OpenAI diarized_json). The format is detected from the content. Returns "
        "vendor-neutral speaker turns, per-speaker summaries with a suggested doctor/patient role, "
        "and warnings. Parsed in memory only: never stored or logged, and vendor metadata (job "
        "names, account ids, request ids) is dropped. Research use only: not a medical device. "
        "Works without the image model loaded."
    ),
)
async def import_transcript(request: Request, file: UploadFile) -> TranscriptImport:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    data = await read_upload(file, settings.max_transcript_mb * 1024 * 1024)
    # JSON parsing of a large file is blocking work: keep it off the event loop.
    return await run_in_threadpool(service.import_transcript, data, settings)

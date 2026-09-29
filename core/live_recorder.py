import asyncio, os, shutil
from pathlib import Path
from core.config import settings
from core.storage import R2Storage
from core.live_source_security import validate_provider_source, ffmpeg_http_args

class LiveRecorder:
    def __init__(self,pool,storage:R2Storage):
        self.pool=pool; self.storage=storage

    async def record(self,session_id,user_id,source,reserved_seconds):
        root=Path(settings.live_work_dir)/str(session_id)
        root.mkdir(parents=True,exist_ok=True)
        segment_pattern=str(root/"segment-%06d.ts")
        max_seconds=min(int(reserved_seconds),settings.live_hard_max_minutes*60)
        source=validate_provider_source(source)
        input_args=ffmpeg_http_args(source.headers)
        cmd=[
          "ffmpeg","-hide_banner","-loglevel","warning","-y",
          "-protocol_whitelist","https,http,tcp,tls,crypto",
          "-rw_timeout","15000000",*input_args,"-i",source.manifest_url,
          "-map","0:v?","-map","0:a?","-c","copy",
          "-f","segment","-segment_time",str(settings.live_segment_seconds),
          "-segment_format","mpegts","-reset_timestamps","1",
          "-t",str(max_seconds),segment_pattern
        ]
        env=os.environ.copy()
        proc=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE,env=env)
        await self.pool.execute(
          "update live_sessions set status='recording',started_at=now(),heartbeat_at=now() where id=$1",session_id)
        uploaded=set(); total_bytes=0; total_seconds=0
        try:
            while proc.returncode is None:
                await asyncio.sleep(2)
                await self.pool.execute("update live_sessions set heartbeat_at=now() where id=$1",session_id)
                files=sorted(root.glob("segment-*.ts"))
                # Never upload the newest file while FFmpeg may still be writing it.
                for f in files[:-1]:
                    idx=int(f.stem.split("-")[-1])
                    if idx in uploaded: continue
                    key=f"live/{user_id}/{session_id}/{f.name}"
                    obj=await self.storage.put_file(f,key,"video/mp2t")
                    duration=settings.live_segment_seconds
                    await self.pool.execute(
                      """insert into live_segments(live_session_id,segment_index,storage_key,size_bytes,duration_seconds)
                      values($1,$2,$3,$4,$5) on conflict do nothing""",
                      session_id,idx,obj.key,obj.size_bytes,duration)
                    uploaded.add(idx); total_bytes+=obj.size_bytes; total_seconds+=duration
                    f.unlink(missing_ok=True)
                if proc.returncode is None:
                    try: await asyncio.wait_for(proc.wait(),timeout=0.01)
                    except asyncio.TimeoutError: pass

            # Flush final completed file after FFmpeg exits.
            for f in sorted(root.glob("segment-*.ts")):
                idx=int(f.stem.split("-")[-1])
                if idx in uploaded or f.stat().st_size==0: continue
                key=f"live/{user_id}/{session_id}/{f.name}"
                obj=await self.storage.put_file(f,key,"video/mp2t")
                duration=min(settings.live_segment_seconds,max(1,max_seconds-total_seconds))
                await self.pool.execute(
                  """insert into live_segments(live_session_id,segment_index,storage_key,size_bytes,duration_seconds)
                  values($1,$2,$3,$4,$5) on conflict do nothing""",
                  session_id,idx,obj.key,obj.size_bytes,duration)
                total_bytes+=obj.size_bytes; total_seconds+=duration
            stderr=(await proc.stderr.read()).decode(errors="replace")[-2000:]
            status="completed" if proc.returncode==0 else "failed"
            await self.pool.execute(
              """update live_sessions set status=$2,ended_at=now(),recorded_seconds=$3,bytes_uploaded=$4,
              stop_reason=$5,error_code=$6 where id=$1""",
              session_id,status,total_seconds,total_bytes,
              "duration_or_source_ended" if status=="completed" else "ffmpeg_failed",
              None if status=="completed" else f"FFMPEG_{proc.returncode}")
            return {"status":status,"seconds":total_seconds,"bytes":total_bytes,"stderr":stderr}
        finally:
            if proc.returncode is None:
                proc.terminate()
                try: await asyncio.wait_for(proc.wait(),timeout=10)
                except asyncio.TimeoutError: proc.kill()
            shutil.rmtree(root,ignore_errors=True)

import asyncio, shutil
from pathlib import Path
from core.config import settings

class LiveFinalizer:
    def __init__(self,pool,storage): self.pool=pool; self.storage=storage
    async def finalize(self,session_id,user_id):
        row=await self.pool.fetchrow("select * from live_sessions where id=$1 and user_id=$2",session_id,user_id)
        if not row: raise ValueError("Live session not found")
        if row["finalize_status"]=="completed": return {"storage_key":row["final_storage_key"],"idempotent":True}
        segs=await self.pool.fetch("select * from live_segments where live_session_id=$1 order by segment_index",session_id)
        if not segs: raise ValueError("No Live segments")
        actual=[int(x["segment_index"]) for x in segs]
        if actual!=list(range(actual[0],actual[-1]+1)): raise ValueError("Segment gap detected")
        root=Path(settings.live_work_dir)/f"finalize-{session_id}"; root.mkdir(parents=True,exist_ok=True)
        await self.pool.execute("update live_sessions set finalize_status='finalizing' where id=$1",session_id)
        try:
            manifest=root/"concat.txt"; lines=[]
            for seg in segs:
                local=root/f"{int(seg['segment_index']):06d}.ts"; await self.storage.download_file(seg["storage_key"],local)
                lines.append(f"file '{local}'")
            manifest.write_text("\n".join(lines)+"\n"); out=root/"final.mp4"
            proc=await asyncio.create_subprocess_exec("ffmpeg","-hide_banner","-loglevel","warning","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy","-movflags","+faststart",str(out),stderr=asyncio.subprocess.PIPE)
            _,err=await proc.communicate()
            if proc.returncode: raise RuntimeError(err.decode(errors="replace")[-1000:])
            obj=await self.storage.put_file(out,f"live/{user_id}/{session_id}/final.mp4","video/mp4")
            await self.pool.execute("""update live_sessions set final_storage_key=$2,final_size_bytes=$3,finalized_at=now(),finalize_status='completed',finalize_error=null where id=$1""",session_id,obj.key,obj.size_bytes)
            return {"storage_key":obj.key,"size_bytes":obj.size_bytes,"idempotent":False}
        except Exception as exc:
            await self.pool.execute("update live_sessions set finalize_status='failed',finalize_error=$2 where id=$1",session_id,str(exc)[:1000]); raise
        finally: shutil.rmtree(root,ignore_errors=True)

import asyncio
from fastapi import FastAPI, HTTPException
import httpx
from upstash_redis import Redis  # Vercel serverless ke liye best

app = FastAPI()

# ================= CONFIGURATION =================
REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"
BASE_URL = "https://sachinacademyapi.classx.co.in"

# Upstash Redis HTTP Client (No TCP/SSL socket errors on Vercel)
redis_client = Redis(url=REDIS_URL, token=REDIS_TOKEN)

# HTTP Client (Vercel me async_client ko function ke andar manage karna safe hota hai)
async_client = httpx.AsyncClient()
EXCLUDE_BATCHES = set()

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

async def fetch_api(endpoint: str, params: dict):
    try:
        response = await async_client.get(f"{BASE_URL}{endpoint}", headers=COMMON_HEADERS, params=params)
        return response.json()
    except Exception as e:
        return {"status": 500, "message": f"API Error: {str(e)}", "data": []}

# ================= CORE LOGIC =================

async def fetch_single_account_batches(token, userid, identifier):
    """
    Batches fetch karega, agar nahi mile toh serverless-safe tarike se
    Redis se data delete kar dega.
    """
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": token, "User-Id": userid})
    
    batches_found = []
    try:
        response = await async_client.get(f"{BASE_URL}/get/mycourseweb", headers=headers, params={"userid": userid})
        
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, dict) and result.get("status") == 200:
                batch_list = result.get("data", [])
                for batch in batch_list:
                    b_id = str(batch.get("id") or batch.get("course_id") or "")
                    course_name = batch.get("course_name", "").strip()
                    course_slug = batch.get("course_slug", "").strip()

                    if not b_id or b_id in EXCLUDE_BATCHES or course_slug in EXCLUDE_BATCHES:
                        continue

                    if "old" in course_name.lower() and "kvs" in course_name.lower() and "interview" in course_name.lower():
                        continue

                    thumbnail = (
                        batch.get("course_thumbnail") or 
                        batch.get("course_image") or 
                        batch.get("cover_image") or 
                        batch.get("thumbnail") or ""
                    ).strip()

                    batches_found.append({
                        "id": b_id,
                        "course_name": course_name,
                        "course_thumbnail": thumbnail
                    })
    except Exception as e:
        print(f"[ERROR] Fetch single account failed for {identifier}: {e}")
    
    # BATCHES NAHI MILE TOH REDIS SE TOKEN DELETE
    if not batches_found:
        print(f"[CLEANUP] No batches found for {identifier}. Deleting keys...")
        try:
            # Sync calls ko loop.run_in_executor me chalana serverless me thik rehta hai
            loop = asyncio.get_event_loop()
            await asyncio.gather(
                loop.run_in_executor(None, redis_client.delete, f"token:{identifier}"),
                loop.run_in_executor(None, redis_client.delete, f"userid:{identifier}")
            )
        except Exception as re:
            print(f"[ERROR] Redis cleanup failed for {identifier}: {re}")
            
    return batches_found


# ================= ENDPOINTS =================

@app.get("/api/add-token")
async def add_manual_token(token: str, userid: str, phone: str = None):
    if not token or not userid:
        raise HTTPException(status_code=400, detail="token and userid are required")

    identifier = phone.strip() if phone else userid.strip()
    try:
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, redis_client.set, f"token:{identifier}", token.strip()),
            loop.run_in_executor(None, redis_client.set, f"userid:{identifier}", userid.strip())
        )
        return {"status": "Success", "message": f"Token saved successfully for {identifier}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save token: {str(e)}")


@app.get("/api/my-batches")
async def get_all_merged_batches():
    combined_data = []
    seen_ids = set()

    try:
        loop = asyncio.get_event_loop()
        # Fetch all keys using Upstash HTTP Client
        all_keys = await loop.run_in_executor(None, redis_client.keys, "token:*")
        
        if not all_keys:
            return {"status": 200, "message": "No tokens found", "data": []}

        identifiers = [key.split(":", 1)[1] for key in all_keys]
        
        # Gathering Redis HTTP Requests efficiently
        token_tasks = [loop.run_in_executor(None, redis_client.get, f"token:{ide}") for ide in identifiers]
        userid_tasks = [loop.run_in_executor(None, redis_client.get, f"userid:{ide}") for ide in identifiers]
        
        tokens = await asyncio.gather(*token_tasks)
        userids = await asyncio.gather(*userid_tasks)

        api_tasks = []
        for ide, t, u in zip(identifiers, tokens, userids):
            if t and u:
                api_tasks.append(fetch_single_account_batches(t, u, ide))

        results = await asyncio.gather(*api_tasks)

        for batch_list in results:
            for b in batch_list:
                if b["id"] not in seen_ids:
                    combined_data.append(b)
                    seen_ids.add(b["id"])

    except Exception as e:
        print(f"[ERROR] Redis processing failed: {e}")

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully", 
        "data": combined_data
    }

@app.get("/api/subjects")
async def get_subjects(courseid: str):
    return await fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})

@app.get("/api/topics")
async def get_topics(courseid: str, subjectid: str):
    return await fetch_api("/get/alltopicfrmlivecourseclass", {"courseid": courseid, "subjectid": subjectid, "start": "-1"})

@app.get("/api/videos")
async def get_videos(courseid: str, subjectid: str, topicid: str):
    return await fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", {
        "courseid": courseid, "subjectid": subjectid, "topicid": topicid, "conceptid": "", "windowsapp": "false", "start": "0"
    })

@app.get("/api/video-details")
async def get_video_details(courseid: str, videoid: str):
    return await fetch_api("/get/fetchVideoDetailsById", {"course_id": courseid, "video_id": videoid, "ytflag": "0", "folder_wise_course": "0"})

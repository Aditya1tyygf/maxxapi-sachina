import asyncio
from fastapi import FastAPI, HTTPException
import httpx
from upstash_redis.asyncio import Redis  # Serverless safe client

app = FastAPI()

# ================= CONFIGURATION =================
REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"
BASE_URL = "https://sachinacademyapi.classx.co.in"

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
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

# Dynamic dynamic mapping check karega courseid ke respect me
async def fetch_api(endpoint: str, params: dict, courseid: str):
    try:
        mapped_token = await redis.get(f"course_token:{courseid}")
        mapped_userid = await redis.get(f"course_userid:{courseid}")
        
        if not mapped_token or not mapped_userid:
            return {"status": 401, "msg": "No mapping found for this course. Hit /api/my-batches first."}
            
        headers = COMMON_HEADERS.copy()
        headers.update({"Authorization": mapped_token, "User-Id": mapped_userid})
        
        response = await async_client.get(f"{BASE_URL}{endpoint}", headers=headers, params=params)
        return response.json()
    except Exception as e:
        return {"status": 500, "message": f"API Error: {str(e)}", "data": []}

# ================= CORE LOGIC =================

async def fetch_single_account_batches(token, userid, identifier):
    """Async API call for Single Account Batches + Auto Mapping & Cleanup"""
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": token, "User-Id": userid})
    
    batches_found = []
    try:
        response = await async_client.get(f"{BASE_URL}/get/mycourseweb", headers=headers, params={"userid": userid})
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, dict) and result.get("status") == 200:
                batch_list = result.get("data", [])
                
                redis_tasks = []
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
                    
                    # Token ko uske courseid se map kar rahe hain taaki subject automatic chal sake
                    redis_tasks.append(redis.set(f"course_token:{b_id}", token))
                    redis_tasks.append(redis.set(f"course_userid:{b_id}", userid))
                    redis_tasks.append(redis.expire(f"course_token:{b_id}", 86400)) # 24 Hours expiry
                    redis_tasks.append(redis.expire(f"course_userid:{b_id}", 86400))
                
                if redis_tasks:
                    await asyncio.gather(*redis_tasks)
    except Exception as e:
        print(f"[ERROR] Fetch single account failed: {e}")
        
    # Agar is token se batches nahi mile, toh token delete kar do
    if not batches_found:
        print(f"[CLEANUP] Deleting useless token for {identifier}")
        await asyncio.gather(
            redis.delete(f"token:{identifier}"),
            redis.delete(f"userid:{identifier}")
        )
        
    return batches_found


# ================= ENDPOINTS =================

@app.get("/api/add-token")
async def add_manual_token(token: str, userid: str, phone: str = None):
    if not token or not userid:
        raise HTTPException(status_code=400, detail="token and userid are required")

    identifier = phone.strip() if phone else userid.strip()
    try:
        await asyncio.gather(
            redis.set(f"token:{identifier}", token.strip()),
            redis.set(f"userid:{identifier}", userid.strip())
        )
        return {"status": "Success", "message": f"Token saved successfully for {identifier}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save token: {str(e)}")


@app.get("/api/my-batches")
async def get_all_merged_batches():
    combined_data = []
    seen_ids = set()

    try:
        all_keys = await redis.keys("token:*")
        if not all_keys:
            return {"status": 200, "message": "No tokens found", "data": []}

        identifiers = [key.split(":", 1)[1] for key in all_keys]
        
        token_tasks = [redis.get(f"token:{ide}") for ide in identifiers]
        userid_tasks = [redis.get(f"userid:{ide}") for ide in identifiers]
        
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
        print(f"[ERROR] Redis or async processing failed: {e}")

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully (Ultra Fast Async Mode)", 
        "data": combined_data
    }


@app.get("/api/subjects")
async def get_subjects(courseid: str):
    return await fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid}, courseid)


@app.get("/api/topics")
async def get_topics(courseid: str, subjectid: str):
    return await fetch_api("/get/alltopicfrmlivecourseclass", {
        "courseid": courseid, 
        "subjectid": subjectid, 
        "start": "-1"
    }, courseid)


@app.get("/api/videos")
async def get_videos(courseid: str, subjectid: str, topicid: str):
    params = {
        "courseid": courseid,
        "subjectid": subjectid,
        "topicid": topicid,
        "conceptid": "",
        "windowsapp": "false",
        "start": "0"
    }
    return await fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params, courseid)


@app.get("/api/video-details")
async def get_video_details(courseid: str, videoid: str):
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/fetchVideoDetailsById", params, courseid)

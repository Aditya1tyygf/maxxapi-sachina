import asyncio
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
import httpx
from upstash_redis.asyncio import Redis

app = FastAPI()

# ================= CONFIGURATION =================
# Active Upstash Credentials
REDIS_URL = "https://amusing-humpback-162221.upstash.io"
REDIS_TOKEN = "gQAAAAAAAnmtAAIgcDI2MGMxOTI5M2QzZDU0MGRhOWMwYmIzNzI4NzMwYWVhNQ"
BASE_URL = "https://sachinacademyapi.classx.co.in"

# 🔒 SECURITY KEY (Headers mein X-API-Key ke sath ye value bhejna)
API_KEY = "Maxxkoogfchahiye@123" 
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

async def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == API_KEY:
        return api_key
    raise HTTPException(status_code=403, detail="Could not validate credentials - Unauthorized")

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

# Dynamic mapping check karega courseid ke respect me
async def fetch_api(endpoint: str, params: dict, courseid: str):
    try:
        mapped_token = await redis.get(f"course_token:{courseid}")
        mapped_userid = await redis.get(f"course_userid:{courseid}")
        
        # Safe string decoding
        if isinstance(mapped_token, bytes):
            mapped_token = mapped_token.decode("utf-8")
        if isinstance(mapped_userid, bytes):
            mapped_userid = mapped_userid.decode("utf-8")
        
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
    """Async API call for Single Account Batches + Auto Mapping"""
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": token, "User-Id": userid})
    
    batches_found = []
    try:
        response = await async_client.get(f"{BASE_URL}/get/mycourseweb", headers=headers, params={"userid": userid})
        print(f"[DEBUG - {identifier}] HTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"[DEBUG - {identifier}] API Response JSON: {result}")
            
            status_code = result.get("status")
            if isinstance(result, dict) and (status_code == 200 or str(status_code) == "200"):
                batch_list = result.get("data", []) or []
                
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
                    
                    # Token mapping for fast lookups
                    redis_tasks.append(redis.set(f"course_token:{b_id}", token))
                    redis_tasks.append(redis.set(f"course_userid:{b_id}", userid))
                    redis_tasks.append(redis.expire(f"course_token:{b_id}", 86400))
                    redis_tasks.append(redis.expire(f"course_userid:{b_id}", 86400))
                
                if redis_tasks:
                    await asyncio.gather(*redis_tasks)
            else:
                print(f"[DEBUG - {identifier}] Unexpected status in JSON: {status_code}")
        else:
            print(f"[ERROR - {identifier}] Non-200 API Response: {response.text}")
            
    except Exception as e:
        print(f"[ERROR - {identifier}] Fetch single account failed: {e}")
        
    return batches_found


# ================= ENDPOINTS =================

# 🔒 SUPER SECURE ALL-TOKENS ENDPOINT: Ab identifiers bhi nahi dikhenge!
@app.get("/api/all-tokens", dependencies=[Depends(get_api_key)])
async def get_all_tokens_in_redis():
    """Redis mein stored total active tokens ka sirf numeric count batayega (100% Safe)"""
    try:
        all_keys = await redis.keys("token:*")
        total_count = len(all_keys) if all_keys else 0
        
        return {
            "status": "Success",
            "total_tokens_count": total_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch token count from Redis: {str(e)}")


@app.get("/api/add-token", dependencies=[Depends(get_api_key)])
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


@app.get("/api/my-batches", dependencies=[Depends(get_api_key)])
async def get_all_merged_batches():
    combined_data = []
    seen_ids = set()

    try:
        all_keys = await redis.keys("token:*")
        print(f"[DEBUG - my-batches] Raw keys from Redis: {all_keys}")
        
        if not all_keys:
            return {"status": 200, "message": "No tokens found in DB", "data": []}

        identifiers = []
        for key in all_keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if ":" in key:
                identifiers.append(key.split(":", 1)[1])
        
        token_tasks = [redis.get(f"token:{ide}") for ide in identifiers]
        userid_tasks = [redis.get(f"userid:{ide}") for ide in identifiers]
        
        tokens = await asyncio.gather(*token_tasks)
        userids = await asyncio.gather(*userid_tasks)

        api_tasks = []
        for ide, t, u in zip(identifiers, tokens, userids):
            decoded_t = t.decode("utf-8") if isinstance(t, bytes) else t
            decoded_u = u.decode("utf-8") if isinstance(u, bytes) else u
            
            if decoded_t and decoded_u:
                api_tasks.append(fetch_single_account_batches(decoded_t, decoded_u, ide))

        results = await asyncio.gather(*api_tasks)

        for batch_list in results:
            for b in batch_list:
                if b["id"] not in seen_ids:
                    combined_data.append(b)
                    seen_ids.add(b["id"])

    except Exception as e:
        print(f"[ERROR] Redis or async processing failed in /my-batches: {e}")

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully (Ultra Fast Async Mode)", 
        "data": combined_data
    }


@app.get("/api/subjects", dependencies=[Depends(get_api_key)])
async def get_subjects(courseid: str):
    return await fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid}, courseid)


@app.get("/api/topics", dependencies=[Depends(get_api_key)])
async def get_topics(courseid: str, subjectid: str):
    return await fetch_api("/get/alltopicfrmlivecourseclass", {
        "courseid": courseid, 
        "subjectid": subjectid, 
        "start": "-1"
    }, courseid)


@app.get("/api/videos", dependencies=[Depends(get_api_key)])
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


@app.get("/api/video-details", dependencies=[Depends(get_api_key)])
async def get_video_details(courseid: str, videoid: str):
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/fetchVideoDetailsById", params, courseid)


# --- Live Stream Endpoints ---

@app.get("/api/live-upcoming", dependencies=[Depends(get_api_key)])
async def get_live_upcoming_courses(courseid: str):
    params = {
        "courseid": courseid,
        "start": "-1"
    }
    return await fetch_api("/get/live_upcoming_course_classv2", params, courseid)


@app.get("/api/previous-live-videos", dependencies=[Depends(get_api_key)])
async def get_previous_live_videos(courseid: str):
    params = {
        "course_id": courseid,
        "start": "0",
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/get_previous_live_videos", params, courseid)

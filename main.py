import asyncio
from fastapi import FastAPI, HTTPException
import httpx
from upstash_redis.asyncio import Redis

app = FastAPI()

# ================= CONFIGURATION =================
REDIS_URL = "https://amusing-humpback-162221.upstash.io"
REDIS_TOKEN = "gQAAAAAAAnmtAAIgcDI2MGMxOTI5M2QzZDU0MGRhOWMwYmIzNzI4NzMwYWVhNQ"
BASE_URL = "https://sachinacademyapi.classx.co.in"

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
async_client = httpx.AsyncClient(timeout=15.0)  # Added 15s timeout for stability
EXCLUDE_BATCHES = set()

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

# ================= HELPER FUNCTIONS =================

def safe_decode(val):
    if isinstance(val, bytes):
        return val.decode("utf-8").strip()
    if isinstance(val, str):
        return val.strip()
    return ""

async def fetch_api(endpoint: str, params: dict, courseid: str):
    try:
        mapped_token = safe_decode(await redis.get(f"course_token:{courseid}"))
        mapped_userid = safe_decode(await redis.get(f"course_userid:{courseid}"))
        
        if not mapped_token or not mapped_userid:
            return {"status": 401, "msg": "No mapping found for this course. Hit /api/my-batches first."}
            
        headers = COMMON_HEADERS.copy()
        headers.update({"Authorization": mapped_token, "User-Id": mapped_userid})
        
        response = await async_client.get(f"{BASE_URL}{endpoint}", headers=headers, params=params)
        return response.json()
    except Exception as e:
        return {"status": 500, "message": f"API Error: {str(e)}", "data": []}


# ================= CORE LOGIC =================

async def fetch_single_account_batches(token: str, userid: str, identifier: str):
    """Async API call for Single Account Batches + Auto Mapping"""
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": token, "User-Id": userid})
    
    batches_found = []
    try:
        response = await async_client.get(f"{BASE_URL}/get/mycourseweb", headers=headers, params={"userid": userid})
        
        if response.status_code == 200:
            result = response.json()
            status_code = result.get("status")
            
            if isinstance(result, dict) and (status_code == 200 or str(status_code) == "200"):
                batch_list = result.get("data", []) or []
                
                redis_tasks = []
                for batch in batch_list:
                    b_id = str(batch.get("id") or batch.get("course_id") or "").strip()
                    course_name = str(batch.get("course_name", "")).strip()
                    course_slug = str(batch.get("course_slug", "")).strip()

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
                    
                    # Store mapping in Redis (Valid for 24 hours)
                    redis_tasks.append(redis.set(f"course_token:{b_id}", token))
                    redis_tasks.append(redis.set(f"course_userid:{b_id}", userid))
                    redis_tasks.append(redis.expire(f"course_token:{b_id}", 86400))
                    redis_tasks.append(redis.expire(f"course_userid:{b_id}", 86400))
                
                if redis_tasks:
                    await asyncio.gather(*redis_tasks)
            else:
                print(f"[DEBUG - {identifier}] ClassX Returned non-200 inside JSON: {status_code}")
        else:
            print(f"[ERROR - {identifier}] HTTP Error: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"[ERROR - {identifier}] Fetch failed: {str(e)}")
        
    return batches_found


# ================= ENDPOINTS =================

@app.get("/api/all-tokens")
async def get_all_tokens_in_redis():
    try:
        all_keys = await redis.keys("token:*")
        total_count = len(all_keys) if all_keys else 0
        return {
            "status": "Success",
            "total_tokens_count": total_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch token count from Redis: {str(e)}")


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
            return {"status": 200, "message": "No tokens found in DB", "data": []}

        identifiers = []
        for key in all_keys:
            decoded_key = safe_decode(key)
            if ":" in decoded_key:
                identifiers.append(decoded_key.split(":", 1)[1])
        
        token_tasks = [redis.get(f"token:{ide}") for ide in identifiers]
        userid_tasks = [redis.get(f"userid:{ide}") for ide in identifiers]
        
        tokens = await asyncio.gather(*token_tasks)
        userids = await asyncio.gather(*userid_tasks)

        api_tasks = []
        processed_accounts = 0

        for ide, t, u in zip(identifiers, tokens, userids):
            decoded_t = safe_decode(t)
            decoded_u = safe_decode(u) or ide  # Fallback: Agar UserID missing hai toh Identifier ko UserID maano

            if decoded_t and decoded_u:
                processed_accounts += 1
                api_tasks.append(fetch_single_account_batches(decoded_t, decoded_u, ide))

        if not api_tasks:
            return {
                "status": 400, 
                "message": f"Found {len(identifiers)} token keys, but failed to extract valid tokens/userids.", 
                "data": []
            }

        results = await asyncio.gather(*api_tasks)

        for batch_list in results:
            for b in batch_list:
                if b.get("id") and b["id"] not in seen_ids:
                    combined_data.append(b)
                    seen_ids.add(b["id"])

    except Exception as e:
        return {"status": 500, "message": f"Internal Error: {str(e)}", "data": []}

    return {
        "status": 200, 
        "message": f"Fetched {len(combined_data)} unique batches from {processed_accounts} active accounts.", 
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


@app.get("/api/live-upcoming")
async def get_live_upcoming_courses(courseid: str):
    params = {
        "courseid": courseid,
        "start": "-1"
    }
    return await fetch_api("/get/live_upcoming_course_classv2", params, courseid)


@app.get("/api/previous-live-videos")
async def get_previous_live_videos(courseid: str):
    params = {
        "course_id": courseid,
        "start": "0",
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/get_previous_live_videos", params, courseid)

import asyncio
from fastapi import FastAPI, HTTPException
import httpx
from upstash_redis import Redis

app = FastAPI(title="Sachin Academy Multi-Token Manager")

# Upstash Redis Configuration
UPSTASH_URL = "https://usable-dogfish-156605.upstash.io"
UPSTASH_TOKEN = "gQAAAAAAAmO9AAIgcDFjY2Y5YWFiODk1ODg0NjJjOWMwZTJjMmRiZTJhMGUxMw"

redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)

BASE_URL = "https://sachinacademyapi.classx.co.in"
EXCLUDE_BATCHES = ["demo", "test"]

def get_headers(token: str):
    return {
        "Authorization": token,
        "Auth-Key": "appxapi",
        "Client-Service": "Appx",
        "Source": "website",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Origin": "https://sachinacademy.classx.co.in",
        "Referer": "https://sachinacademy.classx.co.in/"
    }

def get_any_valid_token():
    """Helper to pick a token safely for other requests"""
    try:
        all_keys = redis.keys("token:*")
        if not all_keys:
            raise HTTPException(status_code=404, detail="No tokens found in database")
        # Redis se direct token fetch
        token_val = redis.get(all_keys[0])
        if not token_val:
             raise HTTPException(status_code=404, detail="Token empty in storage")
        return token_val
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")

async def fetch_single_account_batches(client: httpx.AsyncClient, token: str, userid: str):
    batches_found = []
    headers = get_headers(token)
    try:
        response = await client.get(
            f"{BASE_URL}/get/mycourseweb", 
            headers=headers, 
            params={"userid": userid}, 
            timeout=10
        )
        if response.status_code != 200:
            return batches_found
            
        result = response.json()
        
        # FIX HERE: Response status string ya int kuch bhi ho, ya direct list ho toh accept karega
        status_code = result.get("status") if isinstance(result, dict) else None
        if isinstance(result, dict) and (status_code == 200 or str(status_code) == "200"):
            batch_list = result.get("data", [])
        elif isinstance(result, list):
            batch_list = result
        else:
            batch_list = []

        for batch in batch_list:
            b_id = str(batch.get("id") or batch.get("course_id") or "")
            course_name = batch.get("course_name") or batch.get("title") or batch.get("name") or ""
            course_name = str(course_name).strip()
            course_slug = str(batch.get("course_slug") or "").strip()

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
        print(f"[ERROR] Batch fetch failed: {e}")
        
    return batches_found

# --- ENDPOINTS ---

@app.get("/api/add-token")
def add_manual_token(token: str, userid: str, phone: str = None):
    if not token or not userid:
        raise HTTPException(status_code=400, detail="token and userid are required")

    identifier = phone.strip() if phone else userid.strip()
    try:
        redis.set(f"token:{identifier}", token.strip())
        redis.set(f"userid:{identifier}", userid.strip())
        return {"status": "Success", "message": f"Token saved for {identifier}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upstash error: {str(e)}")

@app.get("/api/my-batches")
async def get_all_merged_batches():
    combined_data = []
    seen_ids = set()

    try:
        all_keys = redis.keys("token:*")
        if not all_keys:
            return {"status": 200, "message": "No tokens found", "data": []}

        identifiers = [key.split(":", 1)[1] for key in all_keys]
        
        # Upstash-redis sync fetching inside async router block
        tokens = [redis.get(f"token:{ide}") for ide in identifiers]
        userids = [redis.get(f"userid:{ide}") for ide in identifiers]

        async with httpx.AsyncClient() as client:
            api_tasks = []
            for t, u in zip(tokens, userids):
                if t and u:
                    api_tasks.append(fetch_single_account_batches(client, t, u))

            results = await asyncio.gather(*api_tasks)

        for batch_list in results:
            for b in batch_list:
                if b["id"] not in seen_ids:
                    combined_data.append(b)
                    seen_ids.add(b["id"])

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merged batches error: {str(e)}")

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully", 
        "data": combined_data
    }

@app.get("/api/subjects")
async def get_subjects(courseid: str):
    token = get_any_valid_token()
    headers = get_headers(token)
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{BASE_URL}/get/allsubjectfrmlivecourseclass", headers=headers, params={"courseid": courseid, "start": "-1"})
        return res.json()

@app.get("/api/topics")
async def get_topics(courseid: str, subjectid: str):
    token = get_any_valid_token()
    headers = get_headers(token)
    params = {"courseid": courseid, "subjectid": subjectid, "start": "-1"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{BASE_URL}/get/alltopicfrmlivecourseclass", headers=headers, params=params)
        return res.json()

@app.get("/api/videos")
async def get_videos(courseid: str, subjectid: str, topicid: str):
    token = get_any_valid_token()
    headers = get_headers(token)
    params = {
        "courseid": courseid,
        "subjectid": subjectid,
        "topicid": topicid,
        "conceptid": "",
        "windowsapp": "false",
        "start": "0"
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{BASE_URL}/get/livecourseclassbycoursesubtopconceptapiv3", headers=headers, params=params)
        return res.json()

@app.get("/api/video-details")
async def get_video_details(courseid: str, videoid: str):
    token = get_any_valid_token()
    headers = get_headers(token)
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0",
        "lc_app_api_url": ""
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{BASE_URL}/get/fetchVideoDetailsById", headers=headers, params=params)
        return res.json()

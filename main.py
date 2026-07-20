import asyncio
import json
import os
import time
from fastapi import FastAPI, HTTPException
import httpx

app = FastAPI()

# ================= CONFIGURATION =================
BASE_URL = "https://sachinacademyapi.classx.co.in"
TOKENS_FILE = "tokens.json"

async_client = httpx.AsyncClient(timeout=15.0)
EXCLUDE_BATCHES = set()

# In-Memory Cache for course mappings & API speed
COURSE_MAPPINGS = {}  # { course_id: {"token": ..., "userid": ...} }
CACHE_TTL = 60  # 60 seconds cache for /api/my-batches
CACHE_DATA = {"timestamp": 0, "response": None}

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

# ================= HELPER FUNCTIONS =================

def load_user_tokens():
    """Local JSON file se tokens load karne ka function"""
    if not os.path.exists(TOKENS_FILE):
        return []
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ERROR] JSON File read failed: {e}")
        return []

async def fetch_api(endpoint: str, params: dict, courseid: str):
    try:
        # Memory mapping check
        mapping = COURSE_MAPPINGS.get(str(courseid))
        if not mapping:
            return {"status": 401, "msg": "No mapping found for this course. Hit /api/my-batches first."}
            
        headers = COMMON_HEADERS.copy()
        headers.update({
            "Authorization": mapping["token"], 
            "User-Id": mapping["userid"]
        })
        
        response = await async_client.get(f"{BASE_URL}{endpoint}", headers=headers, params=params)
        return response.json()
    except Exception as e:
        return {"status": 500, "message": f"API Error: {str(e)}", "data": []}


# ================= CORE LOGIC =================

async def fetch_single_account_batches(token: str, userid: str, identifier: str):
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
                    
                    # Memory mapping save for subsequent API calls
                    COURSE_MAPPINGS[b_id] = {
                        "token": token,
                        "userid": userid
                    }
            
    except Exception as e:
        print(f"[ERROR - {identifier}] Fetch failed: {str(e)}")
        
    return batches_found


# ================= ENDPOINTS =================

@app.get("/api/all-tokens")
async def get_all_tokens_in_json():
    users = load_user_tokens()
    return {
        "status": "Success",
        "total_tokens_count": len(users)
    }


@app.get("/api/my-batches")
async def get_all_merged_batches():
    global CACHE_DATA
    current_time = time.time()

    # Fast In-Memory Cache Check
    if CACHE_DATA["response"] and (current_time - CACHE_DATA["timestamp"] < CACHE_TTL):
        return CACHE_DATA["response"]

    users = load_user_tokens()
    if not users:
        return {"status": 200, "message": "No tokens found in tokens.json file", "data": []}

    combined_data = []
    seen_ids = set()
    api_tasks = []

    for item in users:
        token = str(item.get("token", "")).strip()
        userid = str(item.get("userid", "")).strip()
        phone = str(item.get("phone", "")).strip() or userid

        if token and userid:
            api_tasks.append(fetch_single_account_batches(token, userid, phone))

    if not api_tasks:
        return {"status": 400, "message": "Invalid tokens data format in JSON.", "data": []}

    results = await asyncio.gather(*api_tasks)

    for batch_list in results:
        for b in batch_list:
            if b.get("id") and b["id"] not in seen_ids:
                combined_data.append(b)
                seen_ids.add(b["id"])

    res = {
        "status": 200, 
        "message": f"Fetched {len(combined_data)} unique batches from {len(api_tasks)} JSON accounts.", 
        "data": combined_data
    }
    CACHE_DATA = {"timestamp": current_time, "response": res}
    return res


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
    params = {"courseid": courseid, "start": "-1"}
    return await fetch_api("/get/live_upcoming_course_classv2", params, courseid)


@app.get("/api/previous-live-videos")
async def get_previous_live_videos(courseid: str):
    params = {"course_id": courseid, "start": "0", "folder_wise_course": "0"}
    return await fetch_api("/get/get_previous_live_videos", params, courseid)

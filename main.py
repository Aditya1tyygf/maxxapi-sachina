import asyncio
import httpx
from fastapi import FastAPI, HTTPException
import random

app = FastAPI(title="Sachin Academy Aggregator API (No Redis)")

# ================= CONFIGURATION =================

ACCOUNTS = [
    {"phone": "9140256954", "pass": "Vikas@9651"},
    {"phone": "9508063031", "pass": "Soni@95080"}
]

BASE_URL = "https://sachinacademyapi.classx.co.in"

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

EXCLUDE_BATCHES = {"8", "kvs-interview-batch-old"}

# Reusable Async HTTP Client
async_client = httpx.AsyncClient(timeout=10.0)

# Server ki runtime memory me token to course map karne ke liye dictionary
TOKEN_MAP = {}

# ================= AUTH CORE =================

async def perform_login(phone, password):
    """Account login karke token aur userid nikalega"""
    payload = {
        "source": "website",
        "phone": phone,
        "email": phone,
        "password": password,
        "extra_details": "1"
    }
    try:
        files = {k: (None, v) for k, v in payload.items()}
        resp = await async_client.post(
            f"{BASE_URL}/post/userLogin?extra_details=0", 
            headers=COMMON_HEADERS, 
            files=files, 
            timeout=12.0
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == 200:
            return {
                "token": data["data"]["token"], 
                "userid": str(data["data"]["userid"])
            }
    except Exception as e:
        print(f"[ERROR] Login failed for {phone}: {e}")
    return None


async def fetch_single_account_batches(account):
    """Pehle login karega fir us account ke saare batches nikalega"""
    auth = await perform_login(account["phone"], account["pass"])
    if not auth:
        return []
        
    token = auth["token"]
    userid = auth["userid"]
    
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": token, "User-Id": userid})
    
    batches_found = []
    try:
        response = await async_client.get(f"{BASE_URL}/get/mycourseweb", headers=headers, params={"userid": userid})
        if response.status_code != 200:
            return batches_found
            
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

                # MEMORY MAPPING: Is course id ke liye kaunsa token valid hai, server memory me save kar lo
                TOKEN_MAP[b_id] = {"token": token, "userid": userid}

                batches_found.append({
                    "id": b_id,
                    "course_name": course_name,
                    "course_thumbnail": thumbnail
                })
    except Exception as e:
        print(f"[ERROR] Fetch batches failed for {account['phone']}: {e}")
        
    return batches_found


async def fetch_api(path, params, courseid):
    """Course ID ke basis par runtime me sahi token pick karne ka wrapper"""
    # Memory me check karo ki is course ka token hai ya nahi
    auth = TOKEN_MAP.get(courseid)
    
    # Agar direct hit kiya hai bina /my-batches chalaye, toh pehle account se backup login kar lo
    if not auth:
        print(f"[INFO] Course {courseid} not mapped. Logging in to a random account as fallback.")
        acc = random.choice(ACCOUNTS)
        auth = await perform_login(acc["phone"], acc["pass"])
        
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication failed for accounts.")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"], 
        "User-Id": auth["userid"]
    })
    
    try:
        response = await async_client.get(BASE_URL + path, headers=headers, params=params)
        return response.json()
    except Exception as e:
        return {"error": str(e)}


# ================= ENDPOINTS =================

@app.get("/api/my-batches")
async def get_all_merged_batches():
    """Dono accounts se batches parallel fetch karke merge karega"""
    combined_data = []
    seen_ids = set()

    # Dono accounts par ek sath parallel login aur fetch chalega fast execution ke liye ⚡
    tasks = [fetch_single_account_batches(acc) for acc in ACCOUNTS]
    results = await asyncio.gather(*tasks)

    for batch_list in results:
        for b in batch_list:
            if b["id"] not in seen_ids:
                combined_data.append(b)
                seen_ids.add(b["id"])

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully from ACCOUNTS", 
        "data": combined_data
    }


@app.get("/api/subjects")
async def get_subjects(courseid: str):
    return await fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid}, courseid=courseid)


@app.get("/api/topics")
async def get_topics(courseid: str, subjectid: str):
    return await fetch_api("/get/alltopicfrmlivecourseclass", {
        "courseid": courseid, 
        "subjectid": subjectid, 
        "start": "-1"
    }, courseid=courseid)


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
    return await fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params, courseid=courseid)


@app.get("/api/video-details")
async def get_video_details(courseid: str, videoid: str):
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/fetchVideoDetailsById", params, courseid=courseid)


@app.get("/")
def home():
    return {"status": "Active", "msg": "Sachin Academy Local Multi-Account Aggregator is running smoothly!"}

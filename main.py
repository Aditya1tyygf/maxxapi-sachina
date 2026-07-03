import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from upstash_redis.asyncio import Redis  # Async Redis client
import random

app = FastAPI(title="Sachin Academy Final Aggregator API")

# ================= CONFIGURATION =================

REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"

# Async Redis connection initialize kiya
redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)

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

# Reusable Async HTTP Client to reduce connection handshake overhead
async_client = httpx.AsyncClient(timeout=8.0)

# ================= AUTH CORE (ASYNC) =================

async def perform_login(phone, password):
    payload = {
        "source": "website",
        "phone": phone,
        "email": phone,
        "password": password,
        "extra_details": "1"
    }
    try:
        # httpx me files ki jagah data/form use hota hai multipart ke liye
        files = {k: (None, v) for k, v in payload.items()}
        resp = await async_client.post(
            f"{BASE_URL}/post/userLogin?extra_details=0", 
            headers=COMMON_HEADERS, 
            files=files, 
            timeout=10.0
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == 200:
            token = data["data"]["token"]
            userid = str(data["data"]["userid"])
            
            # Async Redis Set
            await asyncio.gather(
                redis.set(f"token:{phone}", token),
                redis.set(f"userid:{phone}", userid)
            )
            return {"token": token, "userid": userid, "phone": phone}
    except Exception as e:
        print(f"[ERROR] Login failed for {phone}: {e}")
    return None


async def get_valid_auth():
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = await redis.get(f"token:{acc['phone']}")
        userid = await redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid}
    
    new_auth = await perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])
    return new_auth


async def fetch_api(path, params=None, auth_data=None):
    auth = auth_data if auth_data else await get_valid_auth()
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication failed for all accounts.")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"], 
        "User-Id": auth["userid"]
    })
    
    try:
        response = await async_client.get(BASE_URL + path, headers=headers, params=params)
        if response.status_code in [401, 403]:
            return {"error": "reauth_needed"}
        return response.json()
    except Exception as e:
        return {"error": str(e)}


async def fetch_single_account_batches(token, userid):
    """Async API call for Single Account Batches"""
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

                batches_found.append({
                    "id": b_id,
                    "course_name": course_name,
                    "course_thumbnail": thumbnail
                })
    except Exception as e:
        print(f"[ERROR] Fetch single account failed: {e}")
        
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
    """SUPER FAST ASYNC FETCH: Ek baar me saare batches fast parallel process honge"""
    combined_data = []
    seen_ids = set()

    try:
        all_keys = await redis.keys("token:*")
        if not all_keys:
            return {"status": 200, "message": "No tokens found", "data": []}

        # Pipeline technique / Async Gather to fetch all Redis keys instantly
        identifiers = [key.split(":", 1)[1] for key in all_keys]
        
        token_tasks = [redis.get(f"token:{ide}") for ide in identifiers]
        userid_tasks = [redis.get(f"userid:{ide}") for ide in identifiers]
        
        # Ek hi jhatke me saare tokens aur userids Redis se utha liye
        tokens = await asyncio.gather(*token_tasks)
        userids = await asyncio.gather(*userid_tasks)

        # Build execution tasks for third-party API
        api_tasks = []
        for t, u in zip(tokens, userids):
            if t and u:
                api_tasks.append(fetch_single_account_batches(t, u))

        # Saare accounts ke batches ek saath parallel fetch ho rhe hain bina block hue ⚡
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
    return await fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})


@app.get("/api/topics")
async def get_topics(courseid: str, subjectid: str):
    return await fetch_api("/get/alltopicfrmlivecourseclass", {
        "courseid": courseid, 
        "subjectid": subjectid, 
        "start": "-1"
    })


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
    return await fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params)


@app.get("/api/video-details")
async def get_video_details(courseid: str, videoid: str):
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0"
    }
    return await fetch_api("/get/fetchVideoDetailsById", params)


@app.post("/api/login")
async def sign_in_user(phone: str, password: str):
    auth = await perform_login(phone, password)
    if auth:
        return {"status": "Success", "message": "Logged in and Token Saved", "data": auth}
    raise HTTPException(status_code=401, detail="Login Failed")


@app.get("/api/saved-tokens")
async def list_saved_tokens():
    tokens = []
    try:
        keys = await redis.keys("token:*")
        identifiers = [k.split(":", 1)[1] for k in keys]
        
        token_tasks = [redis.get(f"token:{ide}") for ide in identifiers]
        userid_tasks = [redis.get(f"userid:{ide}") for ide in identifiers]
        
        all_tokens = await asyncio.gather(*token_tasks)
        all_userids = await asyncio.gather(*userid_tasks)

        for ident, token, userid in zip(identifiers, all_tokens, all_userids):
            tokens.append({
                "identifier": ident,
                "userid": userid,
                "token_preview": token[:20] + "..." if token else ""
            })
    except Exception as e:
        print(e)
    return {"status": 200, "count": len(tokens), "data": tokens}


@app.get("/")
def home():
    return {"status": "Active", "dev": "Maxx Papa", "msg": "Sachin Academy Aggregator API is running smoothly!"}

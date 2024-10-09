from fastapi import FastAPI, Request, Query, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore, storage
import starlette.status as status
from google.cloud.firestore_v1.base_query import FieldFilter
import local_constants
import os

# Create a FastAPI app instance
app = FastAPI()
firebase_request_adapter = requests.Request()

# Initialize Firestore client with the correct project ID
firestore_db = firestore.Client(project="twitter-52984")
# firestore_db = firestore.Client(project="evproject-417219")


app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory="templates") 
# Define a route for the root URL

def validateFirebaseToken(id_token):
    if not id_token:
        return None

    user_token = None
    try:
        user_token=google.oauth2.id_token.verify_firebase_token(id_token,firebase_request_adapter)   
    except ValueError as err:
        print(str(err))
    return user_token


def get_username_list():
    usernames = []
    # Assuming you have a collection named "users" and each document contains a field "username"
    users_ref = firestore_db.collection('twitter_user')
    docs = users_ref.stream()

    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        usernames.append(data)        
    return usernames

# Get user's tweets
def get_user_tweets(user_id,username):
    tweets_ref = firestore_db.collection("twitter_user").document(user_id).collection("tweets").where("username", "==", username).order_by("date", direction=firestore.Query.DESCENDING).limit(10)
    tweets = [doc.to_dict() for doc in tweets_ref.stream()]
    return tweets


def generate_timeline(user_id):
    
    user_tweets_ref = firestore_db.collection(f'twitter_user/{user_id}/tweets').order_by('date', direction=firestore.Query.DESCENDING).limit(20).stream()
    user_tweets = []

# Iterate over each tweet document
    for doc in user_tweets_ref:
        # Get the tweet data as a dictionary
        tweet_data = doc.to_dict()
        # Include the document ID in the tweet data
        tweet_data['tweetID'] = doc.id
        tweet_data['userID'] = user_id
        # Append the tweet data to the list
        user_tweets.append(tweet_data)

    # print("user_tweets",user_tweets)
    # Retrieve tweets from users the current user is following
    following_ref = firestore_db.collection('twitter_user').document(user_id).get()
    following = following_ref.to_dict().get('followings', [])
    following_tweets = []
    # print("following",following)
    for following_id in following:
        # print("following_id",following_id)
        following_tweets_ref = firestore_db.collection(f'twitter_user/{following_id}/tweets').order_by('date', direction=firestore.Query.DESCENDING).limit(20).stream()
            # Extend the following_tweets list with the tweets
        for doc in following_tweets_ref:
            # Get the tweet data as a dictionary
            tweet_data = doc.to_dict()
            # Include the document ID in the tweet data
            tweet_data['tweetID'] = doc.id
            tweet_data['userID'] = user_id
            following_tweets.append(tweet_data)

    # Combine and sort tweets
    all_tweets = user_tweets + following_tweets
    
    all_tweets.sort(key=lambda x: x['date'], reverse=True)

    # Display the last 20 tweets
    timeline = all_tweets[:20]
    # print("timeline",timeline)
    return timeline

def getTwitterUser(user_token):

    user = firestore_db.collection('twitter_user').document(user_token['user_id'])

    if not user.get().exists:
        user_data = {
        "username":"",
        "email":"",
        "followers": [], 
        "followings": [],
        "profile_url":""
        }
        firestore_db.collection("twitter_user").document(user_token['user_id']).set(user_data)
    return user

def downloadBlob(image_data: list):
    
    images = []
    for data in image_data: 
        if 'image_url' in data:
            image_url = data.get('image_url')
        else:
            image_url = data.get('profile_url')    
        if image_url:
            # Check if the image exists in Firestore Storage
            storage_client = storage.Client(project=local_constants.PROJECT_NAME)
            bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
            blob = bucket.blob(image_url)
            if blob.exists():
                blob.make_public()
                # Get the public URL of the object
                image_url = blob.public_url                              
                images.append({'image_url': image_url})
    return images

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if user_token:
        tweetUser = getTwitterUser(user_token)
        timeline = None
        username_list = get_username_list()
        # print("Usernames:", tweetUser)
        username = user_token.get('email').split('@')[0]
        # print("username*********",username)
        # Check if the email already exists in the 'twitter_user' collection
        user_ref = firestore_db.collection('twitter_user').where('username', '==', username).limit(1).get()
        if not user_ref:
            user_data = {
                "username":username,
                "email":user_token.get('email'),
                "followers": [], 
                "followings": [],
                "profile_url":""
            }
            firestore_db.collection("twitter_user").document(user_token['user_id']).set(user_data)
        else:
            for data in user_ref:
                user_doc_id = data.id
                timeline = generate_timeline(user_doc_id)
                
                image_urls = downloadBlob(timeline)

                if len(timeline) > 0 and len(image_urls) > 0:
                    for tweet, image_url in zip(timeline, image_urls):
                        # tweet['tweet_img'] = image_url
                        tweet['image_url'] = image_url['image_url']
                    
                # print("timeline",timeline)
        return templates.TemplateResponse("home.html", {"request": request, "user_token":user_token,"username_list":username_list,"timeline":timeline})
    else:
        return templates.TemplateResponse("login.html", {"request": request,"user_token": None})

def addDirectory(directory_name):
    
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.blob(directory_name)
    blob.upload_from_string('',content_type="application/x-www-form-urlencoded;charset=UTF-8")


def addFile(file, user_doc_id,tweet_id):
    # Define the directory path
    if tweet_id is None:
        dir_name = 'profileImages/' + user_doc_id + '/'
    else:
        dir_name = 'tweetImages/' + user_doc_id + '/' + tweet_id + '/'
       
    if dir_name =='' or dir_name[-1] != '/':
        print("dir_name",dir_name)
        return RedirectResponse('/')

    # Initialize the storage client
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)

    # Get the bucket
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)

    # Create the directory if it doesn't exist
    addDirectory(dir_name)

    # Save the image file to the directory
    image_path = os.path.join(dir_name, file.filename)

    # Check if the file already exists
    if blob_exists(bucket, image_path):
        # If it exists, delete the existing file
        delete_blob(bucket, image_path)

    blob = bucket.blob(image_path)
    blob.upload_from_file(file.file)

    # Return the path to the saved image
    return image_path

def blob_exists(bucket, blob_name):
    """Check if a blob exists in a bucket."""
    return storage.Blob(bucket=bucket, name=blob_name).exists()

def delete_blob(bucket, blob_name):
    """Delete a blob in a bucket."""
    bucket.blob(blob_name).delete()

@app.post("/tweets")
async def create_tweet(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        message = "To add tweets, please log in or sign up first."
        return templates.TemplateResponse("home.html", {"request": request, "user_token":None,"message":message})
    else:
        form = await request.form()
        image_path = None
        users_ref = firestore_db.collection('twitter_user')
        username = user_token.get('email').split('@')[0]
        query = users_ref.where("username", "==", username).limit(1)

        result = query.stream()

        for data in result:
            user_doc_id = data.id        

        tweets_ref = firestore_db.collection('twitter_user').document(user_doc_id).collection('tweets')
        tweet_data = {
            "tweetText":form['tweetText'] ,
            "username": username,
            "email":user_token.get('email'),
            "date": datetime.utcnow(),                        
        }
        tweet_ref = tweets_ref.add(tweet_data)
        tweet_id = tweet_ref[1].id
        if form['image'].filename:
            image_path = addFile(form['image'],user_doc_id,tweet_id)
            # Update the tweet data with the image_url
            tweet_data["image_url"] = image_path            
            # Set the tweet document in Firestore with the updated data
            tweets_ref.document(tweet_id).set(tweet_data)
        return RedirectResponse("/",status_code=status.HTTP_302_FOUND)

@app.post("/search", response_class=HTMLResponse)
async def search_users(request:Request,username: str = Form(...)):
    # Perform search for usernames in Firestore
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    username_list = get_username_list()
    users_ref = firestore_db.collection('twitter_user')
    query = users_ref.where("username", ">=", username.lower()).where("username", "<=", username.lower() + u"\uf8ff")
    query_result = query.stream()

    matched_usernames = []
    index = 1
    for doc in query_result:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        user_data["index"] = index
        matched_usernames.append(user_data)
        index += 1
    if len(matched_usernames) == 0:
        message="No User found"
        return templates.TemplateResponse("home.html", {"request": request,"userMessage":message,"username":username,"username_list":username_list,"user_token":user_token})
    else:
        return templates.TemplateResponse("home.html", {"request": request, "Search_Data":matched_usernames,"username":username,"username_list":username_list,"user_token":user_token}) 



@app.get("/user_profile", response_class=HTMLResponse)
async def get_user_profile(user_id: str, request: Request): 

    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    users_ref = firestore_db.collection('twitter_user')
    username = user_token.get('email').split('@')[0]
    query = users_ref.where("username", "==", username).limit(1)
    result = query.stream()

    for data in result:
        data_dict = data.to_dict()  # Convert DocumentSnapshot to dictionary
        is_following = user_id in data_dict.get('followings', [])  # Check if user_id is in the 'followings' list
        print("data**********", is_following)

    user_docs = firestore_db.collection("twitter_user").document(user_id).get()
    user_data = None
    user_data = user_docs.to_dict()
    user_data["id"] = user_docs.id
    print("user dataaaa",user_data)
    tweets = get_user_tweets(user_id,user_data['username'])
    
    return templates.TemplateResponse("user_profile.html", {"request": request, "basic_info": user_data, "tweets": tweets,"user_token":user_token,"is_following":is_following})



@app.post("/tweetList", response_class=HTMLResponse)
async def tweet_form(request:Request,user: str = Form(...), tweet: str = Form(...)):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    username_list = get_username_list()
    tweets_ref = firestore_db.collection(f'twitter_user/{user}/tweets')
    query = tweets_ref.where("tweetText", ">=", tweet.lower()).where("tweetText", "<=", tweet.lower() + u"\uf8ff")
    
    query_result = query.stream()

    matched_usernames = []
    index = 1
    for doc in query_result:
        user_data = doc.to_dict()
        user_data['id'] = doc.id
        user_data["index"] = index
        matched_usernames.append(user_data)
        index += 1
    if len(matched_usernames) == 0:
        message="No tweet found for this user"
        return templates.TemplateResponse("home.html", {"request": request,"tweetMessage":message,"tweet":tweet,"selected_user":user,"username_list":username_list,"user_token":user_token})
    else:        
        return templates.TemplateResponse("home.html", {"request": request, "tweet_Data":matched_usernames,"tweet":tweet,"selected_user":user,"username_list":username_list,"user_token":user_token})
        # return RedirectResponse("/",status_code=status.HTTP_302_FOUND)

@app.post("/follow/{following_id}")
def follow_user(request:Request,following_id: str):
    print("following_id",following_id)
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    users_ref = firestore_db.collection('twitter_user')
    username = user_token.get('email').split('@')[0]
    query = users_ref.where("username", "==", username).limit(1)

    result = query.stream()

    for data in result:
        follower_id = data.id
    
    print("follower id",follower_id)
    # Check if the follower and following users exist
    follower_ref = firestore_db.collection('twitter_user').document(follower_id).get()
    following_ref = firestore_db.collection('twitter_user').document(following_id).get()
    if not follower_ref.exists:
        raise HTTPException(status_code=404, detail="Follower not found")
    if not following_ref.exists:
        raise HTTPException(status_code=404, detail="Following not found")

    # Update follower's following list
    firestore_db.collection('twitter_user').document(follower_id).update({
        'followings': firestore.ArrayUnion([following_id])
    })

    # Update following's followers list
    firestore_db.collection('twitter_user').document(following_id).update({
        'followers': firestore.ArrayUnion([follower_id])
    })

    # return templates.TemplateResponse("user_profile.html", {"request": request,"message": "User followed successfully","user_token":user_token})
    return {"message": "User followed successfully"}

# Define the unfollow endpoint
@app.post("/unfollow/{following_id}")
def follow_user(request:Request,following_id: str):
    print("following_id",following_id)
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    users_ref = firestore_db.collection('twitter_user')
    username = user_token.get('email').split('@')[0]
    query = users_ref.where("username", "==", username).limit(1)

    result = query.stream()

    for data in result:
        follower_id = data.id
    
    print("follower id",follower_id)

    # Check if both users exist
    follower_ref = firestore_db.collection('twitter_user').document(follower_id).get()
    following_ref = firestore_db.collection('twitter_user').document(following_id).get()
    if not follower_ref.exists:
        raise HTTPException(status_code=404, detail="Follower not found")
    if not following_ref.exists:
        raise HTTPException(status_code=404, detail="Following not found")

    # Update follower's following list
    firestore_db.collection('twitter_user').document(follower_id).update({
        'followings': firestore.ArrayRemove([following_id])
    })

    # Update following's followers list
    firestore_db.collection('twitter_user').document(following_id).update({
        'followers': firestore.ArrayRemove([follower_id])
    })

    return {"message": "User unfollowed successfully"}


@app.post("/editTweet")
async def edit_tweet(tweetId: str = Form(...),userId: str = Form(...), content: str = Form(...),update_image: UploadFile = File(None)):
    # Dummy function to simulate editing a tweet
    print("tweetid",tweetId)
    print("userId",userId)
    print("content",content)
    # print("file",update_image)
    tweets_ref = firestore_db.collection(f'twitter_user/{userId}/tweets')
    tweet_doc_ref = tweets_ref.document(tweetId)
    image_path = addFile(update_image,userId,tweetId)
    if not tweet_doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Tweet not found")
    tweet_doc_ref.update({"tweetText": content,"image_url": image_path})
    # return {"message": "Tweet edited successfully"}
    return RedirectResponse("/",status_code=status.HTTP_302_FOUND)

@app.post("/edit_profile_image")
async def edit_tweet(request:Request):
# async def edit_tweet(request:Request,userId: str = Form(...),profile_image: UploadFile = File(None)):
    # Dummy function to simulate editing a tweet
    form = await request.form()
    tweetId =None
    print("userId",form['userID'])
    print("profile_image",form['profile_image'])
    
    image_path = addFile(form['profile_image'],form['userID'],tweetId)
    firestore_db.collection('twitter_user').document(form['userID']).update({
        'profile_url': image_path
    })
    # return {"message": "Tweet edited successfully"}
    return RedirectResponse("/",status_code=status.HTTP_302_FOUND)

@app.post("/deleteTweet")
async def delete_tweet(userId: str = Form(...), tweetId: str = Form(...)):
    # Delete tweet from Firestore
    tweets_ref = firestore_db.collection(f'twitter_user/{userId}/tweets')
    tweet_doc_ref = tweets_ref.document(tweetId)
    if not tweet_doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Tweet not found")
    tweet_doc_ref.delete()
    # return {"message": "Tweet deleted successfully"}
    return RedirectResponse("/",status_code=status.HTTP_302_FOUND)

def get_username_from_id(user_id):
    user_doc_ref = firestore_db.collection('twitter_user').document(user_id)
    user_data = user_doc_ref.get().to_dict()
    
    # Extract username from user data
    if user_data:
        return user_data.get('username')
    else:
        return None

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if user_token:
        users_ref = firestore_db.collection('twitter_user')
        username = user_token.get('email').split('@')[0]
        query = users_ref.where("username", "==", username).limit(1)
        result = query.stream()
        
        userData = []
        followingData = []
        followerData = []
        for data in result:
            data_dict = data.to_dict()  
            followings = data_dict.get('followings', [])
            for following_id in followings:
                followingData.append(get_username_from_id(following_id))
            data_dict['followings']=followingData
            data_dict['userId']= data.id
            data_dict['following_count']=len(followings) if followings else 0
            followers = data_dict.get('followers', [])
            for follower_id in followers:
                followerData.append(get_username_from_id(follower_id))
            data_dict['followers']=followerData
            data_dict['follower_count']=len(followers) if followers else 0
            userData.append(data_dict)
            
            image_path = downloadBlob(userData)
            print("data**********", userData,image_path)
            if len(image_path) > 0 :
                image_path = image_path[0]['image_url']
            else:
                image_path = os.path.join('static', 'user.png')
            # print("image path",image_path[0]['image_url'])

    return templates.TemplateResponse("profile.html", {"request": request, "profile": userData,"user_token":user_token,"image_path":image_path})

@app.get("/login", response_class=HTMLResponse)
async def root(request: Request):

    id_token = request.cookies.get("token")
    error_message = "No error here"
    user_token = None

    if id_token:
        try:
            user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
        except ValueError as err:
            print(str(err))

    return templates.TemplateResponse('login.html', {'request': request, 'user_token': user_token, 'error_message': error_message})
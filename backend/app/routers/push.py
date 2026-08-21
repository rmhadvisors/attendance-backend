from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.notification import PushSubscription
from pywebpush import webpush, WebPushException
from app.services.reminder_scheduler import VAPID_PRIVATE_KEY
from urllib.parse import urlparse



router = APIRouter(prefix="/push", tags=["Push Notifications"])

class SubscriptionSchema(BaseModel):
    endpoint: str
    keys: dict

@router.post("/subscribe")
def subscribe(
    sub: SubscriptionSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Clean up old/stale subscriptions for this user so only the active subscription is retained
    db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id
    ).delete(synchronize_session=False)

    p256dh_val = sub.keys.get("p256dh", "")
    auth_val = sub.keys.get("auth", "")

    new_sub = PushSubscription(
        user_id=current_user.id,
        endpoint=sub.endpoint,
        p256dh=p256dh_val,
        auth=auth_val
    )
    db.add(new_sub)

    db.commit()
    return {"message": "Push Subscription saved"}


@router.post("/send-test")
def send_test_push(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    subscriptions = db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id
    ).all()

    if not subscriptions:
        raise HTTPException(
            status_code=400,
            detail="No push subscriptions found for this user. Please enable notifications in your browser first."
        )

    sent_count = 0
    errors = []

    for sub in subscriptions:
        endpoint = sub.endpoint
        parsed = urlparse(endpoint)
        audience = f"{parsed.scheme}://{parsed.netloc}"
        print(f"🌐 Push endpoint: {endpoint}")
        print(f"🎯 VAPID audience: {audience}")
        try:
            res = webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
                },
                data='{"title": "Notification Test 🔔", "body": "Web Push Notifications are working perfectly!"}',
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={
                            "sub": "mailto:admin@glrattendance.com",
                            "aud": audience
                        },
                ttl=60
            )
            if res.status_code in (200, 201):
                sent_count += 1
                print(f"🚀 TEST PUSH SENT to {current_user.name} ({current_user.email}) - Status: {res.status_code}")
        except WebPushException as exc:
            errors.append(str(exc))
            if exc.response is not None and exc.response.status_code in (404, 410):
                db.delete(sub)
                db.commit()

    return {
        "message": f"Test push sent to {sent_count} device(s).",
        "sent_count": sent_count,
        "errors": errors
    }


    

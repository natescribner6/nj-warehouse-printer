import requests
from datetime import datetime, timedelta
import os
import logging


class ShipStationManager:
    """
    A class to manage ShipStation and FedEx operations for hazmat orders.
    """
    
    def __init__(self, logger=None):
        # FedEx configuration
        self.fedex_oauth_url = "https://apis.fedex.com/oauth/token"
        self.fedex_track_url = "https://apis.fedex.com/track/v1/referencenumbers"
        self.fedex_client_id = os.getenv("FEDEX_CLIENT_ID")
        self.fedex_client_secret = os.getenv("FEDEX_CLIENT_SECRET")
        self.fedex_account_number = os.getenv("FEDEX_ACCOUNT_NUMBER")
        
        # ShipStation configuration
        self.shipstation_base_url = "https://ssapi.shipstation.com"
        self.shipstation_auth = f"Basic {os.getenv('SHIPSTATION_V1_API_KEY')}"
        self.hazmat_tag_id = 30829
        
        # Token management
        self._fedex_token = None
        self._fedex_token_at = None
        
        # Logger
        self.logger = logger or logging.getLogger(__name__)
    
    def get_fedex_token(self):
        """Get a valid FedEx access token, refreshing if necessary."""
        # If we have a token and it's <55min old, reuse it
        if self._fedex_token and self._fedex_token_at:
            if datetime.now() - self._fedex_token_at < timedelta(minutes=55):
                return self._fedex_token
        
        # Otherwise, fetch a fresh one
        self.logger.info("🗝️ Fetching new FedEx token…")
        
        try:
            resp = requests.post(
                self.fedex_oauth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.fedex_client_id,
                    "client_secret": self.fedex_client_secret
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            resp.raise_for_status()
            
            token = resp.json()["access_token"]
            self._fedex_token = token
            self._fedex_token_at = datetime.now()
            
            self.logger.info(f"✅ Got FedEx token ({token[:8]}…)")
            return token
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to get FedEx token: {e}")
            raise
    
    def get_tracking_for_order(self, order_id, account_number):
        """Get FedEx tracking number for a given order ID."""
        token = self.get_fedex_token()
        
        # Compute ±1-day window
        today = datetime.now()
        begin = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=3)).strftime("%Y-%m-%d")
        
        payload = {
            "referencesInformation": {
                "type": "INVOICE",
                "value": str(order_id),
                "accountNumber": account_number,
                "shipDateBegin": begin,
                "shipDateEnd": end
            },
            "includeDetailedScans": True
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-locale": "en_US",
            "Authorization": f"Bearer {token}"
        }
        
        self.logger.info(f"→ FedEx lookup for orderId={order_id}, payload={payload}")
        
        try:
            self.logger.info(f"→ FedEx lookup for orderId={order_id}")
            resp = requests.post(self.fedex_track_url, json=payload, headers=headers)
            self.logger.info(f"← FedEx status={resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            results = data.get("output", {}).get("completeTrackResults", [])
            
            if not results:
                return ""
            
            first = results[0]
            tracks = first.get("trackResults", [])
            
            # If FedEx explicitly says "NOTFOUND", skip it
            if tracks and tracks[0].get("error"):
                return ""
            
            # Top-level field for shipped packages
            tn = first.get("trackingNumber")
            if tn and tn != str(order_id):
                return tn
            
            # Fallback to nested trackingNumberInfo
            if tracks:
                info = tracks[0].get("trackingNumberInfo", {})
                track_num = info.get("trackingNumber")
                if track_num and track_num != str(order_id):
                    return track_num
            
            return ""
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error getting tracking for order {order_id}: {e}")
            return ""
    
    def get_hazmat_orders(self):
        """Fetch all hazmat orders from ShipStation."""
        url = f"{self.shipstation_base_url}/orders/listbytag"
        params = {
            'orderStatus': 'awaiting_shipment',
            'tagId': self.hazmat_tag_id,
            'pageSize': 30
        }
        headers = {
            'Authorization': self.shipstation_auth,
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            orders = data.get('orders', [])
            
            self.logger.info(f"Fetched {len(orders)} hazmat orders from ShipStation")
            for o in orders:
                self.logger.info(f" → ShipStation orderId={o['orderId']}")
                
            return orders
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching hazmat orders: {e}")
            return []
    
    def mark_order_as_shipped(self, order_id, tracking_number, carrier_code="usps"):
        """Mark an order as shipped in ShipStation."""
        url = f"{self.shipstation_base_url}/orders/markasshipped"
        headers = {
            'Authorization': self.shipstation_auth,
            'Content-Type': 'application/json'
        }
        
        payload = {
            "orderId": order_id,
            "carrierCode": carrier_code,
            "shipDate": datetime.now().strftime("%Y-%m-%d"),
            "trackingNumber": tracking_number,
            "notifyCustomer": True,
            "notifySalesChannel": True
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            
            self.logger.info(f"Successfully marked order {order_id} as shipped with tracking {tracking_number}")
            return result
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error marking order {order_id} as shipped: {e}")
            return None
    
    def get_processed_orders(self):
        """Get all hazmat orders with their tracking information processed."""
        orders = self.get_hazmat_orders()
        processed_orders = []
        NJ_ID = 248943
        for order in orders:
            wid = order.get("advancedOptions", {}).get("warehouseId")
            acct = self.fedex_account_number_nj if wid == NJ_ID else self.fedex_account_number_co
            tn = self.get_tracking_for_order(order["orderId"], acct)
            self.logger.info(f"Processing orderId={order['orderId']}")
            #tracking_number = self.get_tracking_for_order(order["orderId"])
            
            ship_to = order.get('shipTo', {})
            processed_orders.append({
                'orderId': order['orderId'],
                'orderNumber': order.get('orderNumber'),
                'orderDate': order.get('orderDate'),
                'orderStatus': order.get('orderStatus'),
                'trackingNumber': tn,
                'shipTo': {
                    'name': ship_to.get('name', ''),
                    'company': ship_to.get('company', ''),
                    'street1': ship_to.get('street1', ''),
                    'street2': ship_to.get('street2', ''),
                    'city': ship_to.get('city', ''),
                    'state': ship_to.get('state', ''),
                    'postalCode': ship_to.get('postalCode', ''),
                    'phone': ship_to.get('phone', '')
                },
                'orderTotal': order.get('orderTotal', 0),
                'items': order.get('items', [])
            })
        
        return processed_orders
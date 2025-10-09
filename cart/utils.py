from decimal import Decimal
from typing import Dict, Any, Optional, List
from django.utils import timezone
from django.conf import settings

CART_SESSION_KEY = "shop_cart"

class Cart:
    """Session-based shopping cart"""
    
    def __init__(self, request):
        """Initialize cart"""
        self.session = request.session
        cart = self.session.get(CART_SESSION_KEY)
        
        if cart is None:
            cart = self.session[CART_SESSION_KEY] = {
                'items': {},
                'created_at': timezone.now().isoformat(),
                'updated_at': timezone.now().isoformat()
            }
        self.cart = cart
    
    def add(self, variant_id: int, quantity: int = 1, override_quantity: bool = False):
        """Add variant to cart or update quantity"""
        variant_id_str = str(variant_id)
        
        if variant_id_str not in self.cart['items']:
            self.cart['items'][variant_id_str] = {
                'quantity': 0,
                'added_at': timezone.now().isoformat()
            }
        
        if override_quantity:
            self.cart['items'][variant_id_str]['quantity'] = quantity
        else:
            self.cart['items'][variant_id_str]['quantity'] += quantity
        
        self.cart['updated_at'] = timezone.now().isoformat()
        self.save()
    
    def save(self):
        """Force save session"""
        self.cart['updated_at'] = timezone.now().isoformat()
        self.session[CART_SESSION_KEY] = self.cart
        self.session.modified = True
    
    def remove(self, variant_id: int):
        """Remove variant from cart completely"""
        variant_id_str = str(variant_id)
        
        if variant_id_str in self.cart['items']:
            del self.cart['items'][variant_id_str]
            self.save()
            return True
        return False
    
    def update_quantity(self, variant_id: int, quantity: int):
        """Update quantity for variant"""
        if quantity <= 0:
            self.remove(variant_id)
        else:
            variant_id_str = str(variant_id)
            if variant_id_str in self.cart['items']:
                self.cart['items'][variant_id_str]['quantity'] = quantity
                self.save()
    
    def clear(self):
        """Clear entire cart"""
        self.cart = {
            'items': {},
            'created_at': timezone.now().isoformat(),
            'updated_at': timezone.now().isoformat()
        }
        self.session[CART_SESSION_KEY] = self.cart
        self.session.modified = True
    
    def get_total_items(self) -> int:
        """Total items count"""
        return sum(item['quantity'] for item in self.cart['items'].values())
    
    def get_items(self) -> List[Dict[str, Any]]:
        """Get all cart items with variant details"""
        from shop.models import Variant
        
        if not self.cart['items']:
            return []
        
        variant_ids = list(self.cart['items'].keys())
        variants = Variant.objects.filter(
            id__in=variant_ids,
            is_active=True
        ).select_related('product', 'color_primary', 'color_secondary', 'size').prefetch_related('images')
        
        variants_dict = {str(v.id): v for v in variants}
        
        items = []
        for variant_id_str, item_data in self.cart['items'].items():
            variant = variants_dict.get(variant_id_str)
            if variant:
                quantity = item_data['quantity']
                price = variant.effective_price()
                items.append({
                    'variant': variant,
                    'quantity': quantity,
                    'price': price,
                    'total_price': price * quantity
                })
        
        return items
    
    def get_subtotal(self) -> Decimal:
        """Calculate subtotal"""
        return sum(Decimal(str(item['total_price'])) for item in self.get_items())
    
    def __len__(self):
        """Total items"""
        return self.get_total_items()
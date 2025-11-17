from django.urls import path
from bonita import views

urlpatterns = [
    path("",        views.index_page,              name="bonita_index"),
    path("home/",   views.home_page,               name="bonita_home"),
    path("login/",  views.login_page,              name="bonita_login"),
    path("nuevo/",  views.nuevo_proyecto_page,     name="bonita_nuevo"),
    path("revisar/", views.revisar_proyectos_page, name="bonita_revisar_page"),
    path("pedido/",  views.pedido_page,            name="bonita_pedido_page"),
    path("ver-pedidos/", views.revisar_pedidos_proyecto_page, name="bonita_ver_pedidos_page"),
    path("compromiso/",  views.compromiso_page,    name="bonita_compromiso_page"),  
    path("consejo/", views.consejo_page, name="bonita_consejo_page"),
    path("evaluar/", views.evaluar_propuestas_page, name="bonita_evaluar_propuestas_page"),

]

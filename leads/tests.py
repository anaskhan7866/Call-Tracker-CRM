from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from unittest.mock import patch
import pandas as pd
from leads.models import Contact


class LogoutReturnTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='caller', password='test-password')

	def test_logout_and_login_returns_to_contact_search(self):
		self.client.force_login(self.user)
		contact_url = '/contacts/?page=3&q=Ravi'

		logout_response = self.client.post(reverse('logout'), {'next': contact_url})

		self.assertEqual(
			logout_response.url,
			f"{reverse('login')}?next=%2Fcontacts%2F%3Fpage%3D3%26q%3DRavi",
		)

		login_response = self.client.post(reverse('login'), {
			'username': 'caller',
			'password': 'test-password',
			'next': contact_url,
		})

		self.assertEqual(login_response.url, contact_url)

	def test_logout_rejects_external_return_url(self):
		self.client.force_login(self.user)

		response = self.client.post(reverse('logout'), {
			'next': 'https://example.com/contacts/?q=Ravi',
		})

		self.assertRedirects(response, reverse('landing_page'), fetch_redirect_response=False)

	def test_logout_from_dashboard_returns_to_last_contact_search(self):
		self.client.force_login(self.user)
		session = self.client.session
		session['last_page'] = '3'
		session['last_query'] = 'Ravi'
		session.save()

		response = self.client.post(reverse('logout'), {'next': '/dashboard/'})

		self.assertEqual(
			response.url,
			f"{reverse('login')}?next=%2Fcontacts%2F%3Fpage%3D3%26q%3DRavi",
		)


class ContactUploadTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='uploader', password='test-password')
		self.client.force_login(self.user)

	def make_excel_file(self, rows):
		file_buffer = BytesIO()
		pd.DataFrame(rows, columns=['Cx Name', 'Contact']).to_excel(file_buffer, index=False)
		return SimpleUploadedFile(
			'contacts.xlsx',
			file_buffer.getvalue(),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

	def test_upload_appends_new_contacts_skips_duplicates_and_preserves_existing_data(self):
		existing_contact = Contact.objects.create(
			user=self.user,
			name='Existing Contact',
			phone_number='1111111111',
			call_status='Connected',
			description='Keep this note',
		)
		excel_file = self.make_excel_file([
			('Changed Existing Name', '1111111111'),
			('New Contact', '2222222222'),
			('Duplicate New Contact', '2222222222'),
		])

		response = self.client.post(reverse('upload_excel'), {'excel_file': excel_file})

		self.assertRedirects(response, reverse('contact_list'), fetch_redirect_response=False)
		self.assertEqual(Contact.objects.count(), 2)
		existing_contact.refresh_from_db()
		self.assertEqual(existing_contact.name, 'Existing Contact')
		self.assertEqual(existing_contact.call_status, 'Connected')
		self.assertEqual(existing_contact.description, 'Keep this note')
		new_contact = Contact.objects.get(user=self.user, phone_number='2222222222')
		self.assertEqual(new_contact.user, self.user)
		self.assertEqual(new_contact.call_status, 'Pending')

	def test_same_phone_can_be_uploaded_by_different_users(self):
		other_user = User.objects.create_user(username='other-uploader', password='test-password')
		Contact.objects.create(
			user=other_user,
			name='Other Account Contact',
			phone_number='3333333333',
		)
		excel_file = self.make_excel_file([
			('My Account Contact', '3333333333'),
			('Repeated In My Upload', '3333333333'),
		])

		self.client.post(reverse('upload_excel'), {'excel_file': excel_file})

		self.assertEqual(Contact.objects.filter(phone_number='3333333333').count(), 2)
		self.assertTrue(Contact.objects.filter(user=self.user, phone_number='3333333333').exists())
		self.assertTrue(Contact.objects.filter(user=other_user, phone_number='3333333333').exists())


class ContactPageJumpTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='page-jumper', password='test-password')
		self.client.force_login(self.user)
		Contact.objects.bulk_create([
			Contact(user=self.user, name='Ravi', phone_number=f'555000{i:04d}')
			for i in range(11)
		])

	def test_page_jump_keeps_the_active_search(self):
		response = self.client.get(reverse('contact_list'), {'page': '2', 'q': 'Ravi'})

		self.assertEqual(response.context['page_obj'].number, 2)
		self.assertEqual(response.context['page_obj'].paginator.num_pages, 2)
		self.assertContains(response, 'name="q" value="Ravi"')
		self.assertContains(response, 'name="page" min="1" max="2" value="2"')

	def test_admin_login_with_contacts_next_url_lands_on_dashboard(self):
		admin = User.objects.create_user(
			username='admin-caller',
			password='test-password',
			is_staff=True,
		)

		response = self.client.post(reverse('login'), {
			'username': admin.username,
			'password': 'test-password',
			'next': '/contacts/?page=3&q=Ravi',
		}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.request['PATH_INFO'], reverse('dashboard'))
		self.assertEqual(response.redirect_chain[-1], (reverse('dashboard'), 302))


class TelecallerDashboardKpiTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='dashboard-caller', password='test-password')
		self.client.force_login(self.user)
		Contact.objects.create(user=self.user, name='Pending Lead', phone_number='4440000001')
		Contact.objects.create(
			user=self.user,
			name='Connected Lead',
			phone_number='4440000002',
			call_status='Connected',
		)
		Contact.objects.create(
			user=self.user,
			name='Inactive Pending Lead',
			phone_number='4440000003',
			is_active=False,
		)

	def test_pending_kpi_counts_active_user_leads(self):
		response = self.client.get(reverse('dashboard'))

		self.assertEqual(response.context['total_leads'], 2)
		self.assertEqual(response.context['pending_leads'], 1)
		self.assertEqual(response.context['connected_leads'], 1)
		self.assertEqual(response.context['not_connected_leads'], 0)
		self.assertContains(response, 'Pending')


class ContactCallLinkTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='caller-with-call', password='test-password')
		self.client.force_login(self.user)

	def test_call_link_adds_indian_country_code_and_preserves_existing_code(self):
		Contact.objects.create(user=self.user, name='Local Number', phone_number='9876543210')
		Contact.objects.create(user=self.user, name='Country Coded', phone_number='+919876543211')

		response = self.client.get(reverse('contact_list'), {'page': '1'})

		self.assertContains(response, 'href="tel:+919876543210"')
		self.assertContains(response, 'href="tel:+919876543211"')

	def test_invalid_phone_does_not_get_a_dial_link(self):
		Contact.objects.create(user=self.user, name='Invalid Number', phone_number='12345')

		response = self.client.get(reverse('contact_list'), {'page': '1'})

		self.assertNotContains(response, 'href="tel:+9112345"')
		self.assertContains(response, 'This contact has an invalid phone number')


class AJAXErrorHandlingTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='ajax-caller', password='test-password')
		self.client.force_login(self.user)

	def test_auto_update_does_not_return_unexpected_exception_details(self):
		contact = Contact.objects.create(
			user=self.user,
			name='AJAX Contact',
			phone_number='5551234567',
		)

		with patch.object(Contact, 'save', side_effect=RuntimeError('private database detail')):
			response = self.client.post(
				reverse('auto_update_contact'),
				data={'id': contact.id, 'status': 'Connected'},
				content_type='application/json',
			)

		self.assertEqual(response.status_code, 500)
		self.assertEqual(response.json()['error'], 'Unable to update contact.')
		self.assertNotIn('private database detail', response.content.decode())

	def test_auto_update_rejects_a_non_numeric_contact_id(self):
		response = self.client.post(
			reverse('auto_update_contact'),
			data={'id': 'not-a-number'},
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.json()['error'], 'Invalid contact ID.')

	def test_daily_call_details_does_not_return_unexpected_exception_details(self):
		admin = User.objects.create_user(
			username='ajax-admin',
			password='test-password',
			is_staff=True,
		)
		self.client.force_login(admin)
		contact = Contact.objects.create(
			user=self.user,
			name='Called Contact',
			phone_number='5551234567',
			call_status='Connected',
		)

		with patch('leads.views.timezone.localtime', side_effect=RuntimeError('private time detail')):
			response = self.client.get(reverse('get_daily_call_details'), {
				'user_id': self.user.id,
				'date': contact.last_updated.date().isoformat(),
			})

		self.assertEqual(response.status_code, 500)
		self.assertEqual(response.json()['error'], 'Unable to load call details.')
		self.assertNotIn('private time detail', response.content.decode())

import http from './http'

export const getFoodCategories = () => http.get('/foods/categories')

export const browseFoods = (params) => http.get('/foods/browse', { params })

// The browse endpoint stays lightweight; fetch servings only after the user picks a food.
export const matchFoodByName = (name) => http.get('/foods/match', { params: { name } })
